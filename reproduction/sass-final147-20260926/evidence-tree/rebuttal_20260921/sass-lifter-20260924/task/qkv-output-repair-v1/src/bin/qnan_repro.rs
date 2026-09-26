#[path = "../sass/mod.rs"]
mod sass;

use sass::{lift_sass_text_to_ptx, SassLiftOptions};

const SOURCE: &str = r#"
code for sm_120
.target sm_120
Function : fsel_qnan_allones
/*0050*/ FSEL R5, RZ, -QNAN , P0 ; /* 0xffffffffff057808 */
         /* 0x000fca0000000000 */
/*0060*/ EXIT ; /* 0x000000000000794d */
         /* 0x000fea0003800000 */
.......... 
"#;

fn recover(text: &str) -> sass::lifter::SassLiftResult {
    lift_sass_text_to_ptx(
        text,
        SassLiftOptions {
            sm_version: 120,
            kernel_name: "fsel_qnan_allones".to_string(),
            include_sass_comments: false,
            emit_unsupported_comments: true,
        },
    )
    .expect("controlled cuobjdump text must parse")
}

fn main() {
    let mode = std::env::args()
        .nth(1)
        .expect("baseline or fixed mode required");
    let allones = recover(SOURCE);
    let canonical = recover(&SOURCE.replace("0xffffffffff057808", "0xffc00000ff057808"));
    if mode == "baseline" {
        assert!(
            allones.ptx.contains("selp.b32 %r5, 0, -QNAN, %p0;"),
            "{}",
            allones.ptx
        );
        assert!(
            canonical.ptx.contains("selp.b32 %r5, 0, -QNAN, %p0;"),
            "{}",
            canonical.ptx
        );
        println!("PINNED_ORIGINAL_REPRODUCES_INVALID_SYMBOL");
    } else if mode == "fixed" {
        assert!(allones.diagnostics.is_empty(), "{:?}", allones.diagnostics);
        assert!(
            canonical.diagnostics.is_empty(),
            "{:?}",
            canonical.diagnostics
        );
        assert!(
            allones.ptx.contains("selp.b32 %r5, 0, 0xffffffff, %p0;"),
            "{}",
            allones.ptx
        );
        assert!(
            canonical.ptx.contains("selp.b32 %r5, 0, 0xffc00000, %p0;"),
            "{}",
            canonical.ptx
        );
        let missing = recover(&SOURCE.replace("/* 0xffffffffff057808 */", ""));
        assert_eq!(missing.diagnostics.len(), 1, "{:?}", missing.diagnostics);
        assert!(
            !missing.ptx.contains("selp.b32 %r5, 0, -QNAN"),
            "{}",
            missing.ptx
        );
        println!("ENCODED_PAYLOAD_PRESERVED_AND_MISSING_ENCODING_DIAGNOSED");
    } else {
        panic!("unknown mode: {mode}");
    }
}
