#![allow(dead_code, unused_imports)] // Upstream modules expose more than this diagnostic uses.

mod sass;

use sass::{lift_sass_text_to_ptx, SassLiftOptions, TextDisassemblyParser};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{collections::HashSet, env, fs, io::Write, path::PathBuf, process};

const COMMIT: &str = "c60cfd8ab54fd1ec4512f749ad61cccc4a390a05";
const HELP: &str = "Isolated Concordia SASS→PTX diagnostic (no GPU, binding, or admission)\n\
Usage: hbfsim_sass_recovery_diagnostic --input SASS --kernel EXACT --sm sm_XX --ptx-out PTX --diagnostics-out JSON [--cubin CUBIN] [--quarantine-out PTX]\n\
The input must be saved official cuobjdump --dump-sass text with one exact architecture image and explicit Function header. Any output PTX is RECOVERED_UNVALIDATED.\n";

#[derive(Default)]
struct Args {
    input: Option<PathBuf>,
    kernel: Option<String>,
    sm: Option<String>,
    ptx_out: Option<PathBuf>,
    diagnostics_out: Option<PathBuf>,
    cubin: Option<PathBuf>,
    quarantine_out: Option<PathBuf>,
}

fn parse_args() -> Result<Option<Args>, String> {
    let mut args = Args::default();
    let argv: Vec<String> = env::args().skip(1).collect();
    if argv.len() == 1 && (argv[0] == "--help" || argv[0] == "-h") {
        return Ok(None);
    }
    if argv.iter().any(|x| x == "--help" || x == "-h") {
        return Err("--help cannot be combined with other arguments".into());
    }
    let mut seen = HashSet::new();
    let mut all = argv.into_iter();
    while let Some(key) = all.next() {
            if !seen.insert(key.clone()) { return Err(format!("Repeated option {key}")); }
            let value = all.next().ok_or_else(|| format!("Missing value for {key}"))?;
            match key.as_str() {
                "--input" => args.input = Some(PathBuf::from(value)),
                "--kernel" => args.kernel = Some(value),
                "--sm" => args.sm = Some(value),
                "--ptx-out" => args.ptx_out = Some(PathBuf::from(value)),
                "--diagnostics-out" => args.diagnostics_out = Some(PathBuf::from(value)),
                "--cubin" => args.cubin = Some(PathBuf::from(value)),
                "--quarantine-out" => args.quarantine_out = Some(PathBuf::from(value)),
                _ => return Err(format!("Unknown option {key}")),
            }
    }
    if args.input.is_none() || args.kernel.is_none() || args.sm.is_none()
        || args.ptx_out.is_none() || args.diagnostics_out.is_none()
    {
        return Err("--input, --kernel, --sm, --ptx-out and --diagnostics-out are required".into());
    }
    Ok(Some(args))
}

fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn reason(code: &str, detail: impl Into<String>) -> Value {
    json!({"code": code, "detail": detail.into()})
}

// The first cuobjdump /*hex-address*/ comment is the instruction prefix. The
// following /* 0x... */ encoding line has no such prefix and is not counted.
fn address_line(line: &str) -> Option<u64> {
    let line = line.trim_start();
    let rest = line.strip_prefix("/*")?;
    let (address, _) = rest.split_once("*/")?;
    let address = address.trim();
    if address.is_empty() || !address.chars().all(|c| c.is_ascii_hexdigit()) {
        return None;
    }
    u64::from_str_radix(address, 16).ok()
}

fn encoding_line(line: &str) -> bool {
    let line = line.trim();
    let Some(hex) = line.strip_prefix("/* 0x").and_then(|x| x.strip_suffix("*/")) else { return false; };
    let hex = hex.trim();
    !hex.is_empty() && hex.chars().all(|c| c.is_ascii_hexdigit())
}

fn normalized_output(path: &PathBuf) -> PathBuf {
    let parent = path.parent().filter(|p| !p.as_os_str().is_empty())
        .unwrap_or_else(|| std::path::Path::new("."));
    let parent = fs::canonicalize(parent).unwrap_or_else(|_| parent.to_path_buf());
    parent.join(path.file_name().unwrap_or_default())
}

fn run(args: &Args) -> Value {
    let input = args.input.as_ref().unwrap();
    let kernel = args.kernel.as_ref().unwrap();
    let sm = args.sm.as_ref().unwrap();
    let mut reasons = Vec::<Value>::new();
    let mut upstream = Vec::<Value>::new();
    let mut raw_count = 0usize;
    let mut parsed_count = 0usize;
    let mut input_sha256: Option<String> = None;
    let mut cubin_sha256: Option<String> = None;
    let mut observed_sm: Option<String> = None;
    let mut recovered_ptx: Option<String> = None;

    let input_bytes = match fs::read(input) {
        Ok(bytes) => { input_sha256 = Some(hash(&bytes)); bytes }
        Err(e) => { reasons.push(reason("INPUT_READ_FAILED", e.to_string())); Vec::new() }
    };
    let text = match std::str::from_utf8(&input_bytes) {
        Ok(s) => s,
        Err(e) => { reasons.push(reason("INPUT_NOT_UTF8", e.to_string())); "" }
    };
    if let Some(cubin) = args.cubin.as_ref() {
        match fs::read(cubin) {
            Ok(bytes) => cubin_sha256 = Some(hash(&bytes)),
            Err(e) => reasons.push(reason("CUBIN_READ_FAILED", e.to_string())),
        }
    }
    if kernel.is_empty() || kernel.trim() != kernel {
        reasons.push(reason("INVALID_KERNEL", "Kernel must be a nonempty exact symbol"));
    }
    let output_paths: Vec<(&str, &PathBuf)> = [
        Some(("candidate", args.ptx_out.as_ref().unwrap())),
        Some(("diagnostics", args.diagnostics_out.as_ref().unwrap())),
        args.quarantine_out.as_ref().map(|p| ("quarantine", p)),
    ].into_iter().flatten().collect();
    let normalized: Vec<_> = output_paths.iter().map(|(_, p)| normalized_output(p)).collect();
    for (idx, (name, path)) in output_paths.iter().enumerate() {
        if path.exists() {
            reasons.push(reason("OUTPUT_EXISTS", format!("{name} output already exists: {}", path.display())));
        }
        if normalized[..idx].contains(&normalized[idx]) {
            reasons.push(reason("OUTPUT_COLLISION", format!("{name} output collides with another output")));
        }
        if normalized[idx] == normalized_output(input)
            || args.cubin.as_ref().is_some_and(|p| normalized[idx] == normalized_output(p)) {
            reasons.push(reason("INPUT_OUTPUT_COLLISION", format!("{name} output collides with an input")));
        }
    }
    let sm_number = sm.strip_prefix("sm_").and_then(|x| x.parse::<u32>().ok());
    if sm_number.is_none() || sm.ends_with('a') || sass::SmVersion::from_version(sm_number.unwrap_or(0)).is_none() {
        reasons.push(reason("UNSUPPORTED_SM", "Exact numeric supported sm_ target required; architecture suffixes are not preserved by this upstream lifter"));
    } else if sm_number == Some(121) {
        reasons.push(reason("UNVERIFIED_ARCH_MAPPING", "This upstream version maps internal 121 to Sm120a; external target identity preservation is unverified"));
    }

    let mut images = Vec::<String>::new();
    let mut selected_headers = 0usize;
    let mut current_function: Option<&str> = None;
    let mut raw_addresses = Vec::<u64>::new();
    for line in text.lines() {
        let line = line.trim();
        if let Some(value) = line.strip_prefix("code for ") {
            images.push(value.trim().to_string());
        }
        if let Some(name) = line.strip_prefix("Function :").or_else(|| line.strip_prefix("function :")) {
            let name = name.trim();
            current_function = Some(name);
            if name == kernel { selected_headers += 1; }
        }
        if current_function == Some(kernel.as_str()) {
            if let Some(address) = address_line(line) {
                raw_count += 1;
                raw_addresses.push(address);
                if TextDisassemblyParser::parse_instruction_line(line).is_none() {
                    reasons.push(reason("UNPARSED_INSTRUCTION", format!("0x{address:x}: {line}")));
                }
            } else if !line.is_empty() && line != ".........." && !encoding_line(line)
                && !line.starts_with(".headerflags")
                && !line.starts_with("Function :") && !line.starts_with("function :") {
                reasons.push(reason("UNCLASSIFIED_FUNCTION_LINE", line.to_string()));
            }
        }
    }
    if images.len() != 1 {
        reasons.push(reason("IMAGE_COUNT", format!("Expected one architecture image, found {}", images.len())));
    } else {
        observed_sm = Some(images[0].clone());
        if images[0] != *sm {
            reasons.push(reason("SM_MISMATCH", format!("Input image {} differs from requested {sm}", images[0])));
        }
    }
    if selected_headers != 1 {
        reasons.push(reason("KERNEL_IDENTITY", format!("Expected one exact Function header for {kernel}, found {selected_headers}")));
    }
    if raw_count == 0 { reasons.push(reason("EMPTY_KERNEL", "No instruction rows found for exact kernel")); }

    let parsed = TextDisassemblyParser::parse_cuobjdump_output(text);
    let selected: Vec<_> = parsed.iter().filter(|x| x.function_name.as_deref() == Some(kernel.as_str())).collect();
    parsed_count = selected.len();
    if selected.iter().map(|x| x.address).collect::<Vec<_>>() != raw_addresses {
        reasons.push(reason("PARSER_LOSS", format!("Raw instruction rows {raw_count}; parsed selected instructions {parsed_count}; address sequences differ")));
    }
    // These upstream branches emit comments or a simplified barrier without a
    // diagnostic. Keep the rejection outside the unmodified lifting algorithm.
    let structural_ok = reasons.is_empty();
    let weak_sync: HashSet<&str> = ["BSSY", "BSYNC", "DEPBAR", "BAR", "MEMBAR"].into_iter().collect();
    for inst in &selected {
        if weak_sync.contains(inst.opcode.as_str()) {
            reasons.push(reason("REQUIRES_SEMANTIC_PROOF", format!("{} at 0x{:x}: {}", inst.opcode, inst.address, inst.instruction_text)));
        }
    }

    if structural_ok {
        let options = SassLiftOptions { sm_version: sm_number.unwrap(), kernel_name: kernel.clone(), include_sass_comments: true, emit_unsupported_comments: true };
        match lift_sass_text_to_ptx(text, options) {
            Ok(result) => {
                for d in result.diagnostics {
                    upstream.push(json!({"address": d.address, "opcode": d.opcode, "message": d.message, "instruction_text": d.instruction_text}));
                }
                if !upstream.is_empty() { reasons.push(reason("UPSTREAM_UNSUPPORTED", format!("{} upstream diagnostics", upstream.len()))); }
                recovered_ptx = Some(result.ptx);
            }
            Err(e) => reasons.push(reason("LIFT_FAILED", e)),
        }
    }

    if reasons.is_empty() {
        let destination = args.ptx_out.as_ref().unwrap();
        let write_result = (|| -> Result<(), String> {
            let parent = destination.parent().filter(|p| !p.as_os_str().is_empty())
                .unwrap_or_else(|| std::path::Path::new("."));
            let mut temporary = tempfile::NamedTempFile::new_in(parent).map_err(|e| e.to_string())?;
            temporary.write_all(recovered_ptx.as_ref().unwrap().as_bytes()).map_err(|e| e.to_string())?;
            temporary.persist_noclobber(destination).map_err(|e| e.to_string())?;
            Ok(())
        })();
        if let Err(e) = write_result {
            reasons.push(reason("PTX_WRITE_FAILED", e));
        }
    } else if let (Some(path), Some(ptx)) = (&args.quarantine_out, &recovered_ptx) {
        let write_result = fs::OpenOptions::new().write(true).create_new(true)
            .open(path).and_then(|mut file| file.write_all(ptx.as_bytes()));
        if let Err(e) = write_result {
            reasons.push(reason("QUARANTINE_WRITE_FAILED", e.to_string()));
        }
    }

    let accepted = reasons.is_empty();
    json!({
        "schema": "hbfsim.sass_recovery_diagnostic.v1",
        "status": if accepted { "RECOVERED_UNVALIDATED" } else { "REJECTED" },
        "admitted": false,
        "source": {"repository": "https://github.com/vickiegpt/Concordia", "commit": COMMIT,
                   "sass_path": input, "sass_sha256": input_sha256,
                   "cubin_path": args.cubin, "cubin_sha256": cubin_sha256,
                   "sass_cubin_association": "NOT_VERIFIED"},
        "kernel": kernel, "requested_sm": sm, "observed_sm": observed_sm,
        "raw_instruction_rows": raw_count, "parsed_selected_instructions": parsed_count,
        "upstream_diagnostics": upstream, "reasons": reasons,
        "candidate_ptx_path": if accepted { args.ptx_out.as_ref() } else { None },
        "quarantine_ptx_path": if !accepted && recovered_ptx.is_some() { args.quarantine_out.as_ref() } else { None },
        "semantic_proof": "NOT_RUN", "parameter_abi_proof": "NOT_RUN",
        "resource_metadata_proof": "NOT_RUN", "gpu_correctness": "NOT_RUN",
        "binding": "NOT_RUN", "hbf_service": "NOT_RUN"
    })
}

fn main() {
    let args = match parse_args() {
        Ok(None) => { print!("{HELP}"); return; }
        Ok(Some(args)) => args,
        Err(e) => { eprintln!("{e}\n{HELP}"); process::exit(2); }
    };
    let report = run(&args);
    let encoded = serde_json::to_vec_pretty(&report).expect("JSON serialization");
    let write_result = fs::OpenOptions::new().write(true).create_new(true)
        .open(args.diagnostics_out.as_ref().unwrap())
        .and_then(|mut file| file.write_all(&encoded));
    if let Err(e) = write_result {
        eprintln!("{}", json!({"status":"REJECTED","reasons":[{"code":"DIAGNOSTICS_WRITE_FAILED","detail":e.to_string()}],"report":report}));
        process::exit(3);
    }
    if report["status"] != "RECOVERED_UNVALIDATED" {
        eprintln!("{report}");
        process::exit(1);
    }
}
