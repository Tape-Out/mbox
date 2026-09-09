"""mbox 的行为测试台：门铃与硬件自旋锁。

自旋锁那一条最要紧：**读即取锁**必须在同一次总线访问里完成。锁开着时连读两次，
第一次该拿到 0（没人占，锁归你），第二次该拿到 1（已被占住）。
拆成「读一次再写一次」的实现在这里过不了。

认矩阵：`harts` 与 `locks` 决定例化几个门铃、几把锁——只有一把锁时「另一把锁没被
连累」这一条无从谈起，整段去掉而不是拿同一把锁冒充。`spinlock` 两个方向都跑：
关掉之后第二次读必须还是 0，锁根本没被取走。只测开着的那一半，门控写漏了看不出来。
"""
import json
import pathlib
import sys

out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
out.mkdir(parents=True, exist_ok=True)
cfg = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
label = cfg.get("label", "")
k = cfg.get("knobs", {})
harts = int(k.get("harts", 2))
locks = int(k.get("locks", 32))
spin = bool(k.get("spinlock", True))

NL = chr(10)
want2 = 1 if spin else 0
why2 = ("lock not taken atomically" if spin
        else "spinlock is off yet the lock was taken")
# 只有一把锁的时候没有「另一把」，整段去掉
other = "" if locks < 2 else NL.join([
    "  // 另一把锁不该被连累",
    "  rule other (ph == Other);",
    "    let x <- m.regs.access(RegReq { addr: rLOCK1, write: False,",
    "                                    wdata: 0, wstrb: 4'hF });",
    "    Bool wrong = False;",
    "    if (x.rdata[0] != 0) begin",
    '      $display("FAIL a different lock came back taken");',
    "      wrong = True;",
    "    end",
    "    if (wrong) bad <= True;",
    "    ph <= Release;",
    "  endrule",
    "",
])
after_lock2 = "Other" if locks >= 2 else "Release"

txt = f'''package Mbox{label}Tb;

import RegIf::*;
import Mbox::*;

// 由 tb/mkmboxtb.py 生成，勿手改。
// 这一点：harts={harts} locks={locks} spinlock={spin}

Bit#(12) rDOOR0 = 12'h000;
Bit#(12) rDSTAT = 12'h100;
Bit#(12) rLOCK0 = 12'h200;
Bit#(12) rLOCK1 = 12'h204;

typedef enum {{ Ring, Pend, ClearD, Lock1, Lock2, Other, Release, Relock, Done }}
  Phase deriving (Bits, Eq);

(* synthesize *)
module mkMbox{label}Tb(Empty);
  MboxIfc#(12, 32, {harts}, {locks}) m <- mkMbox(MboxCfg {{ spinlock: {str(spin).title()} }});

  Reg#(Phase)    ph  <- mkReg(Ring);
  Reg#(Bit#(32)) cyc <- mkReg(0);
  Reg#(Bool)     bad <- mkReg(False);
  Reg#(Bool)     sawIrq <- mkReg(False);

  rule watch;
    if (m.irqs != 0) sawIrq <= True;
  endrule

  rule timeout;
    cyc <= cyc + 1;
    if (cyc > 20000) begin
      $display("TIMEOUT in phase %0d", pack(ph));
      $finish(1);
    end
  endrule

  function Action wr(Bit#(12) a, Bit#(32) d) = action
    let _ <- m.regs.access(RegReq {{ addr: a, write: True,
                                    wdata: d, wstrb: 4'hF }});
  endaction;

  rule ring (ph == Ring);
    wr(rDOOR0, 32'h1);         // 敲 0 号核的门铃
    ph <= Pend;
  endrule

  rule pend (ph == Pend);
    let x <- m.regs.access(RegReq {{ addr: rDSTAT, write: False,
                                    wdata: 0, wstrb: 4'hF }});
    if (x.rdata[0] == 1) ph <= ClearD;
  endrule

  rule clearD (ph == ClearD);
    wr(rDSTAT, 32'h1);         // 写一清零
    ph <= Lock1;
  endrule

  // 第一次读锁：没人占，该拿到 0
  rule lock1 (ph == Lock1);
    let x <- m.regs.access(RegReq {{ addr: rLOCK0, write: False,
                                    wdata: 0, wstrb: 4'hF }});
    Bool wrong = False;
    if (x.rdata[0] != 0) begin
      $display("FAIL first lock read gave %0d, want 0", x.rdata[0]);
      wrong = True;
    end
    if (wrong) bad <= True;
    ph <= Lock2;
  endrule

  // 第二次读同一把锁：取锁与读回是同一拍完成的话该拿到 1；
  // 特性关掉时锁根本不该被取走，还是 0
  rule lock2 (ph == Lock2);
    let x <- m.regs.access(RegReq {{ addr: rLOCK0, write: False,
                                    wdata: 0, wstrb: 4'hF }});
    Bool wrong = False;
    if (x.rdata[0] != {want2}) begin
      $display("FAIL second lock read gave %0d, want {want2} ({why2})",
               x.rdata[0]);
      wrong = True;
    end
    if (wrong) bad <= True;
    ph <= {after_lock2};
  endrule

{other}  rule unlock (ph == Release);
    wr(rLOCK0, 0);             // 写 0 放锁
    ph <= Relock;
  endrule

  rule relock (ph == Relock);
    let x <- m.regs.access(RegReq {{ addr: rLOCK0, write: False,
                                    wdata: 0, wstrb: 4'hF }});
    Bool wrong = False;
    if (x.rdata[0] != 0) begin
      $display("FAIL lock still held after being released");
      wrong = True;
    end
    if (wrong) bad <= True;
    ph <= Done;
  endrule

  rule fin (ph == Done);
    if (!sawIrq) begin
      $display("FAIL doorbell never raised an interrupt");
      bad <= True;
    end
    if (bad || !sawIrq) $display("FAILED");
    else $display("PASS mbox: doorbell, and the lock is taken by the read itself");
    $finish((bad || !sawIrq) ? 1 : 0);
  endrule
endmodule

endpackage
'''
(out / f"Mbox{label}Tb.bsv").write_text(txt, encoding="utf-8")
print(f"  mbox 行为测试台就位：harts={harts} locks={locks} spinlock={spin}")
