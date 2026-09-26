#[path = "../sass/mod.rs"]
mod sass;

use sass::{lift_sass_text_to_ptx, SassLiftOptions};

fn main() {
    let text = r#"code for sm_120
.target sm_120
Function : cs2r_pair
/*0230*/ CS2R R16, SRZ ; /* 0x0000000000107805 */
/*0240*/ ISETP.GT.AND P0, PT, R17, R10, PT ; /* 0x0000000a1100720c */
/*0250*/ EXIT ;
..........
"#;
    let lifted = lift_sass_text_to_ptx(
        text,
        SassLiftOptions {
            sm_version: 120,
            kernel_name: "cs2r_pair".to_string(),
            include_sass_comments: true,
            emit_unsupported_comments: true,
        },
    )
    .expect("original QKV CS2R instruction should parse");
    assert!(lifted.diagnostics.is_empty(), "{:?}", lifted.diagnostics);
    assert!(lifted
        .ptx
        .contains("mov.u32 %r16, 0;\n    mov.u32 %r17, 0;"));
    assert!(lifted.ptx.contains("setp.gt.s32 %p0, %r17, %r10;"));

    let single = r#"Function : cs2r_explicit_32
/*0000*/ CS2R.32 R4, SRZ ;
/*0010*/ EXIT ;
"#;
    let lifted32 = lift_sass_text_to_ptx(single, SassLiftOptions::default())
        .expect("explicit CS2R.32 should parse");
    assert!(lifted32.diagnostics.is_empty(), "{:?}", lifted32.diagnostics);
    assert!(lifted32.ptx.contains("mov.u32 %r4, 0;"));
    assert!(!lifted32.ptx.contains("mov.u32 %r5, 0;"));
    println!("QKV_CS2R_SRZ_PAIR_AND_EXPLICIT_32_PASS");
}
