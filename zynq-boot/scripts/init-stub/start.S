/*
 * start.S — minimal Zynq-7000 (Cortex-A9) startup for the PS-init stub (CLI route only).
 *
 * Sets the stack, zeroes .bss, calls main(). Enough for a poke-and-spin ps7_init; it is
 * deliberately tiny and does NOT set up caches/MMU/vectors. For a more robust build use
 * the Vitis platform route (README.md), which links the full standalone crt0.
 */
    .section .vectors, "ax"
    .global _start
_start:
    ldr  sp, =_stack_top

    /* zero .bss so ps7_init's globals start clean */
    ldr  r0, =__bss_start
    ldr  r1, =__bss_end
    mov  r2, #0
1:  cmp  r0, r1
    bcs  2f
    str  r2, [r0], #4
    b    1b

2:  bl   main
.Lhang:
    wfe
    b    .Lhang
