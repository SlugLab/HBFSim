#[path = "../sass/mod.rs"]
mod sass;

use sass::{lift_sass_text_to_ptx, SassLiftOptions};

// These two instruction lines, including machine-code comments, are from the
// exact QKV SASS that led to the faulting input-vector index.
const SOURCE: &str = r#"
code for sm_120
.target sm_120
Function : hi_repro
/*04a0*/ IMAD.HI.U32 R19, R19, R21, R18 ; /* 0x0000001513137227 */
/*04c0*/ IMAD.HI.U32 R19, R19, R21, RZ ; /* 0x0000001513137227 */
/*7bd0*/ EXIT ; /* 0x000000000000794d */
..........
"#;

fn main() {
    let lifted = lift_sass_text_to_ptx(
        SOURCE,
        SassLiftOptions {
            sm_version: 120,
            kernel_name: "hi_repro".to_string(),
            include_sass_comments: true,
            emit_unsupported_comments: true,
        },
    )
    .expect("controlled original SASS text must parse");
    assert!(lifted.diagnostics.is_empty(), "{:?}", lifted.diagnostics);
    assert!(lifted.ptx.contains("mad.hi.u32 %r19, %r19, %r21, %r18;"));
    assert!(lifted.ptx.contains("mad.hi.u32 %r19, %r19, %r21, 0;"));
    assert!(!lifted.ptx.contains("mad.lo.u32 %r19"));
    let a = 0xffff_ffff_u64;
    let b = 2_u64;
    let c = 3_u32;
    let hi = ((a * b) >> 32) as u32;
    let lo = (a * b) as u32;
    assert_eq!(hi.wrapping_add(c), 4);
    assert_eq!(lo.wrapping_add(c), 1);
    println!("QKV_IMAD_HI_U32_EXACT_FORMS_AND_DISTINCT_SEMANTICS_PASS");
}
