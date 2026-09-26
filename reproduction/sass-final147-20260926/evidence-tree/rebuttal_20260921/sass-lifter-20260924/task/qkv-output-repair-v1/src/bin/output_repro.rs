#[path = "../sass/mod.rs"]
mod sass;

use sass::{lift_sass_text_to_ptx, SassLiftOptions};

fn main() {
    // The output-selection and BF16-pack instructions are copied from the
    // exact QKV SASS. Other arithmetic is intentionally outside this fixture.
    let text = r#"code for sm_120
.target sm_120
Function : output_repro
/*7ac0*/ FSEL R0, RZ, -QNAN , P1 ; /* 0xffffffffff007808 */
/*7ae0*/ FSETP.GEU.AND P0, PT, R5, R0, PT ; /* 0x000000000500720b */
/*7b00*/ FSEL R0, R0, R5, !P0 ; /* 0x0000000500007208 */
/*7b30*/ F2FP.BF16.F32.PACK_AB R0, RZ, R0 ; /* 0x00000000ff00723e */
/*7b40*/ EXIT ;
..........
"#;
    let lifted = lift_sass_text_to_ptx(
        text,
        SassLiftOptions {
            sm_version: 120,
            kernel_name: "output_repro".to_string(),
            include_sass_comments: true,
            emit_unsupported_comments: true,
        },
    )
    .expect("exact output SASS forms should parse");
    assert!(lifted.diagnostics.is_empty(), "{:?}", lifted.diagnostics);
    assert!(lifted.ptx.contains("selp.b32 %r0, 0, 0xffffffff, %p1;"));
    assert!(lifted.ptx.contains("setp.geu.f32 %p0, %r5, %r0;"));
    assert!(lifted.ptx.contains("selp.b32 %r0, %r5, %r0, %p0;"));
    assert!(lifted
        .ptx
        .contains("cvt.rn.bf16x2.f32 %r0, 0f00000000, %r0;"));
    assert!(!lifted.ptx.contains("setp.eq.f32 %p0, %r5, %r0;"));
    assert!(!lifted.ptx.contains("cvt.rn.f16x2.f32 %r0"));
    println!("QKV_OUTPUT_GEU_AND_BF16_EXACT_FORMS_PASS");
}
