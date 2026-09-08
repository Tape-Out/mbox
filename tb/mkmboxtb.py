"""mbox 的行为测试台：门铃与硬件自旋锁。

自旋锁那一条最要紧：**读即取锁**必须在同一次总线访问里完成。这里连读两次，
第一次该拿到 0（没人占，锁归你），第二次该拿到 1（已被占住）。
拆成「读一次再写一次」的实现在这里过不了。
"""
import pathlib
import sys

out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
out.mkdir(parents=True, exist_ok=True)

(out / "MboxTb.bsv").write_text('''package MboxTb;

import RegIf::*;
import Mbox::*;

// 由 tb/mkmboxtb.py 生成，勿手改。

Bit#(12) rDOOR0 = 12'h000;
Bit#(12) rDOOR1 = 12'h004;
Bit#(12) rDSTAT = 12'h100;
Bit#(12) rLOCK0 = 12'h200;
Bit#(12) rLOCK1 = 12'h204;

typedef enum { Ring, Pend, ClearD, Lock1, Lock2, Other, Release, Relock, Done }
  Phase deriving (Bits, Eq);

(* synthesize *)
module mkMboxTb(Empty);
  MboxIfc#(12, 32, 2, 32) m <- mkMbox(MboxCfg { spinlock: True });

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
    let _ <- m.regs.access(RegReq { addr: a, write: True,
                                    wdata: d, wstrb: 4'hF });
  endaction;

  rule ring (ph == Ring);
    wr(rDOOR0, 32'h1);         // 敲 0 号核的门铃
    ph <= Pend;
  endrule

  rule pend (ph == Pend);
    let x <- m.regs.access(RegReq { addr: rDSTAT, write: False,
                                    wdata: 0, wstrb: 4'hF });
    if (x.rdata[0] == 1) ph <= ClearD;
  endrule

  rule clearD (ph == ClearD);
    wr(rDSTAT, 32'h1);         // 写一清零
    ph <= Lock1;
  endrule

  // 第一次读锁：没人占，该拿到 0，同时锁被占上
  rule lock1 (ph == Lock1);
    let x <- m.regs.access(RegReq { addr: rLOCK0, write: False,
                                    wdata: 0, wstrb: 4'hF });
    Bool wrong = False;
    if (x.rdata[0] != 0) begin
      $display("FAIL first lock read gave %0d, want 0", x.rdata[0]);
      wrong = True;
    end
    if (wrong) bad <= True;
    ph <= Lock2;
  endrule

  // 第二次读同一把锁：该拿到 1，说明取锁与读回是同一拍完成的
  rule lock2 (ph == Lock2);
    let x <- m.regs.access(RegReq { addr: rLOCK0, write: False,
                                    wdata: 0, wstrb: 4'hF });
    Bool wrong = False;
    if (x.rdata[0] != 1) begin
      $display("FAIL second lock read gave %0d, want 1 (lock not taken atomically)",
               x.rdata[0]);
      wrong = True;
    end
    if (wrong) bad <= True;
    ph <= Other;
  endrule

  // 另一把锁不该被连累
  rule other (ph == Other);
    let x <- m.regs.access(RegReq { addr: rLOCK1, write: False,
                                    wdata: 0, wstrb: 4'hF });
    Bool wrong = False;
    if (x.rdata[0] != 0) begin
      $display("FAIL a different lock came back taken");
      wrong = True;
    end
    if (wrong) bad <= True;
    ph <= Release;
  endrule

  rule unlock (ph == Release);
    wr(rLOCK0, 0);             // 写 0 放锁
    ph <= Relock;
  endrule

  rule relock (ph == Relock);
    let x <- m.regs.access(RegReq { addr: rLOCK0, write: False,
                                    wdata: 0, wstrb: 4'hF });
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
''', encoding="utf-8")
print("  mbox 行为测试台就位")
