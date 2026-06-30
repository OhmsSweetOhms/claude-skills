/*
 * init_stub.c — minimal Zynq-7000 PS-init stub.
 *
 * Brings the PS up (clocks, DDR, MIO incl. QSPI) by calling the board's generated
 * ps7_init(), then SITS FOREVER. It never reads the boot device, so when used as the
 * "--fsbl" input to jtag_qspi_flash there is NO race to halt — you stop it at leisure
 * and dow U-Boot over it. This is the JTAG-handoff behavior the stock Xilinx FSBL uses
 * in JTAG boot mode (FsblHandoffJtagExit == while(1)), pinned regardless of the strap.
 *
 * ps7_init()/ps7_post_config() come from ps7_init.c, generated for YOUR board (in the
 * .xsa hardware export / Vitis standalone BSP). Drop ps7_init.c + ps7_init.h alongside
 * this file (CLI route) or let Vitis include them (platform route). See README.md.
 *
 * STATUS: authored, NOT yet hardware-verified.
 */
#include "ps7_init.h"

int main(void)
{
    ps7_init();          /* clocks, DDR, MIO (incl. QSPI) — direct register writes */
    ps7_post_config();   /* PL level shifters / EMIO, if the design has them */

    for (;;) {           /* sit: the debugger takes over from here */
        __asm__ volatile ("wfe");   /* low-power spin; any halt/dow works the same */
    }
    return 0;            /* unreachable */
}
