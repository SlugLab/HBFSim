#[path = "../sass/mod.rs"]
mod sass;

use sass::{lift_sass_text_to_ptx, SassLiftOptions};

// Exact instruction forms occur in the captured QKV kernel. This only tests
// pair provenance in the emitter, not GPU behavior or full-kernel equivalence.
const SOURCE: &str = r#"
code for sm_120
.target sm_120
Function : pair_repro
/*0210*/ LDC.64 R10, c[0x0][0x388] ; /* 0x0000e200ff0a7b82 */
/*02b0*/ IMAD.WIDE.U32 R60, R10, UR8, R60 ; /* 0x000000080a3c7c25 */
/*0600*/ LDCU.64 UR4, c[0x0][0x380] ; /* 0x00007000ff0477ac */
/*0630*/ IADD.64 R18, R60, R62 ; /* 0x0000003e3c127235 */
/*0660*/ IADD.64 R18, R18, UR4 ; /* 0x0000000412127c35 */
/*06c0*/ LDG.E.EF.U16 R17, desc[UR6][R18.64] ; /* 0x0000000612117981 */
/*7b40*/ STG.E.U16 desc[UR6][R2.64], R0 ; /* 0x0000000002007986 */
/*7bd0*/ EXIT ; /* 0x000000000000794d */
..........
"#;

fn main() {
    let result = lift_sass_text_to_ptx(
        SOURCE,
        SassLiftOptions {
            sm_version: 120,
            kernel_name: "pair_repro".to_string(),
            include_sass_comments: true,
            emit_unsupported_comments: true,
        },
    )
    .expect("controlled cuobjdump text must parse");
    assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
    let p = &result.ptx;
    for expected in [
        "ld.param.u64 %rd10, [in];\n    mov.b64 {%r10, %r11}, %rd10;",
        "mov.b64 %rd60, {%r60, %r61};",
        "mov.b64 {%r60, %r61}, %rd60;",
        "mov.b64 %rd62, {%r62, %r63};",
        "add.u64 %rd18, %rd60, %rd62;",
        "mov.b64 {%r18, %r19}, %rd18;",
        "mov.b64 %rd18, {%r18, %r19};",
        "mov.b64 %rd18, {%r18, %r19};\n    ld.global.u16 %r17, [%rd18];",
        "mov.b64 %rd2, {%r2, %r3};\n    st.global.u16 [%rd2], %r0;",
    ] {
        assert!(p.contains(expected), "missing {expected}\n{p}");
    }
    assert!(!p.contains("add.s32 %r18"), "IADD.64 fell through to scalar add");
    println!("QKV_PAIR_FORMS_LIFTED_WITH_GPR_ALIAS_BRIDGES");
}
