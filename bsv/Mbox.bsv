package Mbox;

import Vector::*;
import RegIf::*;
import MboxRegs::*;

// 本包不认识任何总线：对外只给中立的 RegIf，接哪种总线由 wrap 或装配决定。
typedef struct {
  Bool spinlock;
} MboxCfg;

interface MboxIfc#(numeric type aw, numeric type dw,
                   numeric type harts, numeric type locks);
  interface RegIf#(aw, dw) regs;
  // 每个核一根中断线。取锁不产生中断——那是软件自旋的事。
  (* always_ready *) method Bit#(harts) irqs;
endinterface

module mkMbox#(MboxCfg cfg)(MboxIfc#(aw, dw, harts, locks))
    provisos (Mul#(TDiv#(dw, 8), 8, dw), Add#(_a, 12, aw), Add#(_b, 1, dw),
              Add#(_c, 8, dw), Add#(_d, harts, 8),
              Log#(TAdd#(harts, 1), _e));

  MboxRegsIfc#(aw, dw, harts, locks) r <- mkMboxRegs(
      MboxRegsCfg { spinlock: cfg.spinlock });

  // 敲过的门铃收进 dstat，再把门铃自己清掉，好让同一个核能连敲两下。
  // 一拍只清一个：同拍敲的都会进 dstat，清的顺序不影响谁被记下。
  rule ring;
    Bit#(8) hit = 0;
    Bit#(TLog#(TAdd#(harts, 1))) idx = 0;
    Bool any = False;
    for (Integer i = 0; i < valueOf(harts); i = i + 1)
      if (r.doorbell[i] == 1) begin
        hit[i] = 1;
        if (!any) begin
          idx = fromInteger(i);
          any = True;
        end
      end
    r.dstat_set(hit);
    if (any) r.doorbell_in(idx, 0);
  endrule

  interface regs = r.regs;
  method Bit#(harts) irqs = truncate(r.dstat);
endmodule

endpackage
