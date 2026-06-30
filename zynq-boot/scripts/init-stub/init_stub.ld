/*
 * init_stub.ld — link the PS-init stub to run from Zynq-7000 OCM (CLI route).
 *
 * Must run from OCM, not DDR: ps7_init() is what brings DDR up, so the code can't live
 * there yet. OCM is mapped low (0x00000000) at reset, like the FSBL. 192 KB is plenty.
 */
MEMORY { OCM (rwx) : ORIGIN = 0x00000000, LENGTH = 0x00030000 }   /* 192 KB low OCM */
ENTRY(_start)

SECTIONS
{
    .text 0x00000000 : {
        *(.vectors)
        *(.text*)
        *(.rodata*)
    } > OCM

    .data : { *(.data*) } > OCM

    .bss : {
        . = ALIGN(4);
        __bss_start = .;
        *(.bss*)
        *(COMMON)
        . = ALIGN(4);
        __bss_end = .;
    } > OCM

    . = ALIGN(8);
    _stack_top = ORIGIN(OCM) + LENGTH(OCM);   /* full descending stack at top of OCM */
}
