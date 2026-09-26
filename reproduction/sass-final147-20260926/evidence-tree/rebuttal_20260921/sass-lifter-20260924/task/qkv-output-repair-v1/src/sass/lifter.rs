use std::{
    collections::{BTreeMap, HashSet},
    io::Write,
    path::Path,
    process::Command,
};

use super::{
    CubinKernel, CubinParser, EnhancedSassInstruction, ParsedCubin, SassDataType, SassDisassembler,
    SassMemorySpace, SassOpcodeClass, SassOperand, SassRegister, TextDisassemblyParser,
};

#[derive(Debug, Clone)]
pub struct SassLiftOptions {
    pub sm_version: u32,
    pub kernel_name: String,
    pub include_sass_comments: bool,
    pub emit_unsupported_comments: bool,
}

impl Default for SassLiftOptions {
    fn default() -> Self {
        Self {
            sm_version: 120,
            kernel_name: String::new(),
            include_sass_comments: true,
            emit_unsupported_comments: true,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SassLiftDiagnostic {
    pub address: Option<u64>,
    pub opcode: String,
    pub message: String,
    pub instruction_text: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SassLiftResult {
    pub ptx: String,
    pub diagnostics: Vec<SassLiftDiagnostic>,
}

pub fn lift_instructions_to_ptx(
    instructions: &[EnhancedSassInstruction],
    options: &SassLiftOptions,
) -> SassLiftResult {
    let mut ctx = LiftContext::new(options);
    ctx.emit_module(instructions);
    SassLiftResult {
        ptx: ctx.output,
        diagnostics: ctx.diagnostics,
    }
}

pub fn lift_sass_text_to_ptx(
    text: &str,
    mut options: SassLiftOptions,
) -> Result<SassLiftResult, String> {
    let instructions = TextDisassemblyParser::parse_cuobjdump_output(text);
    if instructions.is_empty() {
        return Err("No SASS instructions parsed from text input".to_string());
    }

    if is_unspecified_kernel_name(&options.kernel_name) {
        let function_groups = group_sass_text_functions(&instructions);
        if function_groups.len() > 1 {
            return Ok(lift_function_groups_to_ptx(&function_groups, &options));
        }
    }

    let (kernel_name, instructions) =
        select_sass_text_instructions(&instructions, &options.kernel_name)?;
    options.kernel_name = kernel_name;

    Ok(lift_instructions_to_ptx(&instructions, &options))
}

pub fn lift_cubin_to_ptx(
    cubin_data: &[u8],
    mut options: SassLiftOptions,
) -> Result<SassLiftResult, String> {
    if let Some(cuobjdump_path) = std::env::var_os("HETGPU_SASS_LIFTER_CUOBJDUMP") {
        if !cuobjdump_path.is_empty() {
            return lift_cubin_to_ptx_with_cuobjdump(
                cubin_data,
                options,
                Path::new(&cuobjdump_path),
            );
        }
    }

    let parsed = CubinParser::new(cubin_data.to_vec())
        .parse()
        .map_err(|e| format!("Failed to parse CUBIN: {}", e))?;
    let kernel = select_cubin_kernel(&parsed, &options.kernel_name)?;

    options.kernel_name = kernel.name.clone();
    if options.sm_version == 0 {
        options.sm_version = kernel.sm_version;
    }

    let disassembler = SassDisassembler::new(kernel.sm_version)
        .map_err(|e| format!("Failed to create SASS disassembler: {}", e))?;
    let mut instructions = disassembler.disassemble(&kernel.code, kernel.address);
    for inst in &mut instructions {
        inst.function_name = Some(kernel.name.clone());
        if let Some(debug_info) = parsed.debug_lines.get(&inst.address) {
            inst.ptx_file = Some(debug_info.file.clone());
            inst.ptx_line = Some(debug_info.line);
            inst.ptx_column = Some(debug_info.column);
        }
    }

    Ok(lift_instructions_to_ptx(&instructions, &options))
}

pub fn lift_cubin_to_ptx_with_cuobjdump(
    cubin_data: &[u8],
    mut options: SassLiftOptions,
    cuobjdump_path: impl AsRef<Path>,
) -> Result<SassLiftResult, String> {
    let cuobjdump_path = cuobjdump_path.as_ref();
    let mut cubin_file = tempfile::Builder::new()
        .prefix("hetgpu-sass-lifter-")
        .suffix(".cubin")
        .tempfile()
        .map_err(|err| format!("Failed to create temporary CUBIN file: {err}"))?;
    cubin_file
        .write_all(cubin_data)
        .map_err(|err| format!("Failed to write temporary CUBIN file: {err}"))?;
    cubin_file
        .flush()
        .map_err(|err| format!("Failed to flush temporary CUBIN file: {err}"))?;

    let output = Command::new(cuobjdump_path)
        .arg("--dump-sass")
        .arg(cubin_file.path())
        .output()
        .map_err(|err| {
            format!(
                "Failed to execute cuobjdump '{}': {err}",
                cuobjdump_path.display()
            )
        })?;

    let stdout = String::from_utf8(output.stdout)
        .map_err(|err| format!("cuobjdump stdout was not UTF-8: {err}"))?;
    let stderr = String::from_utf8_lossy(&output.stderr);
    if !output.status.success() {
        return Err(format!(
            "cuobjdump '{}' failed with status {}: {}",
            cuobjdump_path.display(),
            output.status,
            stderr.trim()
        ));
    }

    if options.sm_version == 0 {
        options.sm_version = infer_sm_version_from_cuobjdump_text(&stdout)
            .ok_or_else(|| "cuobjdump output did not include an sm_ target".to_string())?;
    }

    lift_sass_text_to_ptx(&stdout, options)
}

fn infer_sm_version_from_cuobjdump_text(text: &str) -> Option<u32> {
    text.split(|ch: char| !(ch.is_ascii_alphanumeric() || ch == '_'))
        .find_map(|token| token.strip_prefix("sm_")?.parse::<u32>().ok())
}

fn select_sass_text_instructions(
    instructions: &[EnhancedSassInstruction],
    requested_kernel_name: &str,
) -> Result<(String, Vec<EnhancedSassInstruction>), String> {
    if !is_unspecified_kernel_name(requested_kernel_name) {
        let selected: Vec<_> = instructions
            .iter()
            .filter(|inst| inst.function_name.as_deref() == Some(requested_kernel_name))
            .cloned()
            .collect();
        if selected.is_empty() {
            return Err(format!(
                "No SASS instructions parsed for kernel '{}'",
                requested_kernel_name
            ));
        }
        return Ok((requested_kernel_name.to_string(), selected));
    }

    let function_names: HashSet<&str> = instructions
        .iter()
        .filter_map(|inst| inst.function_name.as_deref())
        .collect();

    match function_names.len() {
        0 => Ok(("kernel".to_string(), instructions.to_vec())),
        1 => {
            let function_name = function_names.iter().next().copied().unwrap();
            let selected = instructions
                .iter()
                .filter(|inst| inst.function_name.as_deref() == Some(function_name))
                .cloned()
                .collect();
            Ok((function_name.to_string(), selected))
        }
        _ => Err("Multiple SASS functions parsed; set kernel_name to select one".to_string()),
    }
}

fn group_sass_text_functions(
    instructions: &[EnhancedSassInstruction],
) -> Vec<(String, Vec<EnhancedSassInstruction>)> {
    let mut groups: Vec<(String, Vec<EnhancedSassInstruction>)> = Vec::new();
    for inst in instructions {
        let name = inst
            .function_name
            .clone()
            .unwrap_or_else(|| "kernel".to_string());
        if let Some((_, group)) = groups
            .iter_mut()
            .find(|(group_name, _)| *group_name == name)
        {
            group.push(inst.clone());
        } else {
            groups.push((name, vec![inst.clone()]));
        }
    }
    groups
}

fn lift_function_groups_to_ptx(
    groups: &[(String, Vec<EnhancedSassInstruction>)],
    options: &SassLiftOptions,
) -> SassLiftResult {
    let mut ctx = LiftContext::new(options);
    ctx.emit_header();
    for (index, (kernel_name, instructions)) in groups.iter().enumerate() {
        if index > 0 {
            ctx.output.push('\n');
        }
        ctx.emit_entry(kernel_name, instructions);
    }
    SassLiftResult {
        ptx: ctx.output,
        diagnostics: ctx.diagnostics,
    }
}

/// CUBIN parsing already provides kernel boundaries, so selection is a simple
/// metadata lookup before disassembling the chosen kernel's code bytes.
fn select_cubin_kernel<'a>(
    parsed: &'a ParsedCubin,
    requested_kernel_name: &str,
) -> Result<&'a CubinKernel, String> {
    if is_unspecified_kernel_name(requested_kernel_name) {
        return parsed
            .kernels
            .first()
            .ok_or_else(|| "No kernels found in CUBIN".to_string());
    }

    parsed
        .kernels
        .iter()
        .find(|kernel| kernel.name == requested_kernel_name)
        .ok_or_else(|| format!("No kernel named '{}' found in CUBIN", requested_kernel_name))
}

fn is_unspecified_kernel_name(kernel_name: &str) -> bool {
    kernel_name.is_empty()
}

struct LiftContext<'a> {
    options: &'a SassLiftOptions,
    output: String,
    diagnostics: Vec<SassLiftDiagnostic>,
    branch_targets: HashSet<u64>,
    scratch_gpr: Option<String>,
    scratch_gpr2: Option<String>,
    scratch_b64: Option<String>,
    call_state: Option<CallLoweringState>,
    cuda_params: Vec<CudaParamDecl>,
    uses_cuda_param_abi: bool,
    uses_shared_memory: bool,
}

#[derive(Debug, Clone)]
struct CallLoweringState {
    sp_reg: String,
    ret_pc_reg: String,
    pred_reg: String,
    stack_regs: Vec<String>,
    call_sites: BTreeMap<u64, CallSiteLowering>,
}

#[derive(Debug, Clone)]
struct CallSiteLowering {
    target: u64,
    return_id: u32,
    continuation_label: String,
}

impl CallLoweringState {
    fn new(base_gpr: u32, pred_reg: u32, sites: Vec<(u64, u64)>) -> Self {
        let stack_depth = sites.len().max(1);
        let mut call_sites = BTreeMap::new();
        for (index, (address, target)) in sites.into_iter().enumerate() {
            let return_id = index as u32 + 1;
            call_sites.insert(
                address,
                CallSiteLowering {
                    target,
                    return_id,
                    continuation_label: format!("L_callret_{:04x}", address),
                },
            );
        }

        Self {
            sp_reg: format!("%r{}", base_gpr),
            ret_pc_reg: format!("%r{}", base_gpr + 1),
            pred_reg: format!("%p{}", pred_reg),
            stack_regs: (0..stack_depth)
                .map(|slot| format!("%r{}", base_gpr + 2 + slot as u32))
                .collect(),
            call_sites,
        }
    }

    fn gpr_count(&self) -> u32 {
        2 + self.stack_regs.len() as u32
    }
}

impl<'a> LiftContext<'a> {
    fn new(options: &'a SassLiftOptions) -> Self {
        Self {
            options,
            output: String::new(),
            diagnostics: Vec::new(),
            branch_targets: HashSet::new(),
            scratch_gpr: None,
            scratch_gpr2: None,
            scratch_b64: None,
            call_state: None,
            cuda_params: Vec::new(),
            uses_cuda_param_abi: false,
            uses_shared_memory: false,
        }
    }

    fn emit_module(&mut self, instructions: &[EnhancedSassInstruction]) {
        self.emit_header();
        self.emit_entry(&self.options.kernel_name, instructions);
    }

    fn emit_header(&mut self) {
        self.output
            .push_str(ptx_version_for_sm(self.options.sm_version));
        self.output.push('\n');
        self.output
            .push_str(&format!(".target sm_{}\n", self.options.sm_version));
        self.output.push_str(".address_size 64\n\n");
    }

    fn emit_entry(&mut self, kernel_name: &str, instructions: &[EnhancedSassInstruction]) {
        self.branch_targets.clear();
        self.scratch_gpr = None;
        self.scratch_gpr2 = None;
        self.scratch_b64 = None;
        self.call_state = None;
        self.cuda_params.clear();
        self.uses_cuda_param_abi = false;
        self.uses_shared_memory = false;

        self.collect_branch_targets(instructions);
        self.cuda_params = cuda_param_decls(instructions);
        self.uses_cuda_param_abi = !self.cuda_params.is_empty();
        self.uses_shared_memory = uses_shared_memory(instructions);
        let mut regs = RegisterDecls::from_instructions(instructions);
        if needs_gpr_scratch(instructions) {
            self.scratch_gpr = Some(format!("%r{}", regs.max_gpr));
            regs.max_gpr += 1;
        }
        if needs_second_gpr_scratch(instructions) {
            self.scratch_gpr2 = Some(format!("%r{}", regs.max_gpr));
            regs.max_gpr += 1;
        }
        let call_sites = local_call_sites(instructions);
        if !call_sites.is_empty() {
            let call_state = CallLoweringState::new(regs.max_gpr, regs.max_pred, call_sites);
            regs.max_gpr += call_state.gpr_count();
            regs.max_pred += 1;
            self.call_state = Some(call_state);
        }
        regs.max_b64 = regs.max_b64.max(regs.max_gpr);
        if regs.max_gpr > 0 {
            regs.max_b64 = regs.max_b64.max(16);
        }
        if self.uses_cuda_param_abi || self.uses_shared_memory {
            regs.max_b64 = regs.max_b64.max(16);
        }
        if instructions.iter().any(|inst| (inst.opcode == "IADD" && is_64bit_modifier(inst))
            || (inst.opcode == "IMAD" && has_modifier(inst, "WIDE"))) {
            self.scratch_b64 = Some(format!("%rd{}", regs.max_b64));
            regs.max_b64 += 1;
        }

        if self.uses_cuda_param_abi {
            self.emit_param_entry_header(kernel_name);
        } else {
            self.output.push_str(&format!(
                ".visible .entry {}()\n{{\n",
                sanitize_ident(kernel_name)
            ));
        }

        if regs.max_gpr > 0 {
            self.output
                .push_str(&format!("    .reg .b32 %r<{}>;\n", regs.max_gpr));
        }
        if regs.max_uniform_gpr > 0 {
            self.output
                .push_str(&format!("    .reg .b32 %ur<{}>;\n", regs.max_uniform_gpr));
        }
        if regs.max_pred > 0 {
            self.output
                .push_str(&format!("    .reg .pred %p<{}>;\n", regs.max_pred));
        }
        if regs.max_uniform_pred > 0 {
            self.output
                .push_str(&format!("    .reg .pred %up<{}>;\n", regs.max_uniform_pred));
        }
        if regs.max_b64 > 0 {
            self.output
                .push_str(&format!("    .reg .b64 %rd<{}>;\n", regs.max_b64));
        }
        if self.uses_shared_memory {
            self.output
                .push_str("    .shared .align 4 .b8 scratch[512];\n");
        }
        if regs.has_decls() || self.uses_shared_memory {
            self.output.push('\n');
        }
        if let Some(call_state) = &self.call_state {
            self.output
                .push_str(&format!("    mov.u32 {}, 0;\n\n", call_state.sp_reg));
        }

        for inst in instructions {
            self.output
                .push_str(&format!("{}:\n", label_for_address(inst.address)));
            if self.options.include_sass_comments {
                self.output.push_str(&format!(
                    "    // 0x{:04x}: {}\n",
                    inst.address, inst.instruction_text
                ));
            }
            if let Some(line) = self.lift_instruction(inst) {
                self.output.push_str(&format!("    {}\n", line));
            }
        }

        self.output.push_str("}\n");
    }

    fn emit_param_entry_header(&mut self, kernel_name: &str) {
        self.output.push_str(&format!(
            ".visible .entry {}(\n",
            sanitize_ident(kernel_name)
        ));
        for (idx, param) in self.cuda_params.iter().enumerate() {
            let comma = if idx + 1 == self.cuda_params.len() {
                ""
            } else {
                ","
            };
            self.output.push_str(&format!(
                "    .param .{} {}{}\n",
                param.width.ptx_type(),
                cuda_param_name(param.index, param.width),
                comma
            ));
        }
        self.output.push_str(")\n{\n");
    }

    fn collect_branch_targets(&mut self, instructions: &[EnhancedSassInstruction]) {
        for inst in instructions {
            if matches!(
                inst.opcode_class,
                SassOpcodeClass::Branch | SassOpcodeClass::ConditionalBranch
            ) {
                if let Some(target) = branch_target(inst) {
                    self.branch_targets.insert(target);
                }
            }
        }
    }

    fn lift_instruction(&mut self, inst: &EnhancedSassInstruction) -> Option<String> {
        let pred = predicate_prefix(inst);
        match inst.opcode.as_str() {
            "CS2R" if cs2r_srz_pair_form(inst) => {
                let SassOperand::Register(dst) = &inst.dest_operands[0] else {
                    unreachable!();
                };
                Some(format!(
                    "{}mov.u32 %r{}, 0;\n    {}mov.u32 %r{}, 0;",
                    pred, dst.number, pred, dst.number + 1
                ))
            }
            "CS2R" if cs2r_reads_srz(inst) && !has_modifier(inst, "32") => self.unsupported(
                inst,
                "unrecognized 64-bit CS2R SRZ destination form",
            ),
            "S2R" | "S2UR" | "CS2R" => Some(format!(
                "{}mov.u32 {}, {};",
                pred,
                dest_operand(inst).unwrap_or_else(|| "%r0".to_string()),
                inst.src_operands
                    .first()
                    .map(format_operand)
                    .unwrap_or_else(|| "%tid.x".to_string())
            )),
            "MOV" | "MOV32I" => Some(unary_op(
                inst,
                &pred,
                "mov",
                &data_type_suffix(inst, SassDataType::U32),
            )),
            "UMOV" => Some(unary_op(inst, &pred, "mov", "u32")),
            "ULEA" => Some(ulea_op(inst, &pred)),
            "LEA" => Some(lea_op(inst, &pred)),
            "IADD" if is_64bit_modifier(inst) => self
                .scratch_b64
                .as_deref()
                .and_then(|scratch| iadd_64_op(inst, &pred, scratch))
                .or_else(|| self.unsupported(inst, "IADD.64 register-pair form is not supported")),
            "IADD" => Some(binary_op(
                inst,
                &pred,
                "add",
                &data_type_suffix(inst, SassDataType::S32),
            )),
            "IADD3" if is_extended_iadd3(inst) => Some(iadd3_extended_op(
                inst,
                &pred,
                &data_type_suffix(inst, SassDataType::U32),
            )),
            "IADD3" if inst.src_operands.len() == 3 => Some(iadd3_op(
                inst,
                &pred,
                &data_type_suffix(inst, SassDataType::S32),
                self.scratch_gpr.as_deref(),
            )),
            "IADD3" if inst.src_operands.len() < 3 => Some(binary_op(
                inst,
                &pred,
                "add",
                &data_type_suffix(inst, SassDataType::S32),
            )),
            "IADD3" => self.unsupported(inst, "IADD3 extended operand lifting is not implemented"),
            "IMUL" => Some(binary_op(
                inst,
                &pred,
                "mul.lo",
                &data_type_suffix(inst, SassDataType::S32),
            )),
            "IMAD" if has_modifier(inst, "WIDE") && !supported_imad_wide_pair_form(inst) =>
                self.unsupported(inst, "IMAD.WIDE operand form is not supported by pair repair"),
            "IMAD" if has_modifier(inst, "WIDE") => self
                .scratch_b64
                .as_deref()
                .map(|scratch| imad_wide_op(inst, &pred, scratch))
                .or_else(|| self.unsupported(inst, "IMAD.WIDE pair scratch is unavailable")),
            "IMAD" if has_modifier(inst, "HI") && !has_modifier(inst, "U32") =>
                self.unsupported(inst, "IMAD.HI form other than U32 is not supported"),
            "IMAD" if has_modifier(inst, "HI") => Some(imad_op(inst, &pred, "u32", "hi")),
            "IMAD" => Some(imad_op(
                inst,
                &pred,
                &data_type_suffix(inst, SassDataType::S32),
                "lo",
            )),
            "SHL" => Some(binary_op(inst, &pred, "shl", "b32")),
            "SHR" => Some(binary_op(
                inst,
                &pred,
                "shr",
                &data_type_suffix(inst, SassDataType::U32),
            )),
            "SHF" => Some(shf_op(inst, &pred, self.scratch_gpr.as_deref())),
            "USHF" => Some(ushf_op(inst, &pred)),
            "VIMNMX" | "UVIMNMX" if vimnmx_static_operator(inst).is_some() => {
                Some(vimnmx_op(inst, &pred))
            }
            "VIMNMX" | "UVIMNMX" => self.unsupported(
                inst,
                "integer min/max dynamic selector lifting is not implemented",
            ),
            "LOP" if is_lop_lut(inst) => Some(lop3_lut_op(inst, &pred)),
            "LOP" if lop_binary_operator(inst).is_some() => Some(lop_binary_op(inst, &pred)),
            "LOP" => self.unsupported(inst, "logical operation lifting is not implemented"),
            "LOP3" if is_lop3_odd_predicate(inst) => Some(lop3_odd_predicate_op(
                inst,
                &pred,
                self.scratch_gpr.as_deref(),
            )),
            "LOP3" if is_lop3_predicate_lut(inst) => Some(lop3_predicate_lut_op(
                inst,
                &pred,
                self.scratch_gpr.as_deref(),
            )),
            "LOP3" if is_lop3_and(inst) => Some(lop3_binary_op(inst, &pred, "and")),
            "LOP3" if is_lop3_or(inst) => Some(lop3_binary_op(inst, &pred, "or")),
            "LOP3" if is_lop3_xor(inst) => Some(lop3_xor_op(inst, &pred)),
            "LOP3" if is_lop3_lut(inst) => Some(lop3_lut_op(inst, &pred)),
            "LOP3" => self.unsupported(inst, "LOP3 truth-table lifting is not implemented"),
            "ULOP3" if is_ulop3_lut(inst) => Some(lop3_lut_op(inst, &pred)),
            "ULOP3" => self.unsupported(inst, "uniform LOP3 lifting is not implemented"),
            "POPC" => Some(unary_op(inst, &pred, "popc", "b32")),
            "PRMT" => Some(ternary_op(inst, &pred, "prmt", "b32")),
            "FADD" => Some(float_binary_op(
                inst,
                &pred,
                "add",
                &data_type_suffix(inst, SassDataType::F32),
                self.scratch_gpr.as_deref(),
            )),
            "FMUL" => Some(float_binary_op(
                inst,
                &pred,
                "mul",
                &data_type_suffix(inst, SassDataType::F32),
                self.scratch_gpr.as_deref(),
            )),
            "FFMA" => Some(float_ternary_op(
                inst,
                &pred,
                "fma.rn",
                &data_type_suffix(inst, SassDataType::F32),
            )),
            "HFMA2" => Some(hfma2_constant_op(inst, &pred)),
            "F2FP" if has_modifier(inst, "PACK_AB") => Some(f2fp_pack_ab_op(inst, &pred)),
            "F2FP" => self.unsupported(inst, "F2FP sub-operation lifting is not implemented"),
            "HADD2" => Some(hadd2_op(inst, &pred, self.scratch_gpr.as_deref())),
            "HMUL2" => Some(hmul2_op(inst, &pred, self.scratch_gpr.as_deref())),
            "IABS" => Some(unary_op(inst, &pred, "abs", "s32")),
            "I2F" | "I2FP" | "UI2F" | "UI2FP" => Some(i2fp_op(inst, &pred)),
            "UIMAD" => Some(ternary_op(inst, &pred, "mad.lo", "u32")),
            "UIADD3" => Some(uiadd3_op(inst, &pred)),
            "F2I" => Some(f2i_op(inst, &pred)),
            "FRND" if has_modifier(inst, "TRUNC") => Some(frnd_trunc_op(inst, &pred)),
            "FRND" => self.unsupported(inst, "floating round mode lifting is not implemented"),
            "FMNMX" => Some(fmnmx_op(inst, &pred, self.scratch_gpr.as_deref())),
            "FSEL" => fsel_op(inst, &pred).or_else(|| {
                self.unsupported(
                    inst,
                    "FSEL -QNAN requires an encoded negative quiet-NaN immediate",
                )
            }),
            "FABS" => Some(unary_op(
                inst,
                &pred,
                "abs",
                &data_type_suffix(inst, SassDataType::F32),
            )),
            "FNEG" => Some(unary_op(
                inst,
                &pred,
                "neg",
                &data_type_suffix(inst, SassDataType::F32),
            )),
            "LDG" if is_wide_memory_data(inst) => self.unsupported(inst, "wide global data load is not supported by pair repair"),
            "LDG" | "LDS" | "LDL" => Some(load_op(inst, &pred)),
            "LDC" | "LDCU" => Some(ldc_op(inst, &pred)),
            "ST" | "STG" if is_wide_memory_data(inst) => self.unsupported(inst, "wide global data store is not supported by pair repair"),
            "ST" | "STG" | "STS" | "STL" => Some(store_op(inst, &pred)),
            "ATOMG" | "ATOMS" => Some(atomic_op(inst, &pred)),
            "IDP" if is_idp_4a_s8_s8(inst) => Some(idp_4a_s8_s8_op(inst, &pred)),
            "IDP" => self.unsupported(inst, "integer dot-product mode lifting is not implemented"),
            "ISETP" | "FSETP" => Some(setp_op(
                inst,
                &pred,
                self.scratch_gpr.as_deref(),
                self.scratch_gpr2.as_deref(),
            )),
            "UISETP" => Some(uisetp_op(inst, &pred)),
            "SEL" => Some(sel_op(inst, &pred)),
            "USEL" => Some(usel_op(inst, &pred)),
            "PLOP3" if is_plop3_binary_lut(inst) => Some(plop3_binary_lut_op(inst, &pred)),
            "PLOP3" => self.unsupported(inst, "predicate LOP3 lifting is not implemented"),
            "PSETP" => self.unsupported(inst, "predicate set lifting is not implemented"),
            "R2P" => Some(r2p_op(inst, &pred, self.scratch_gpr.as_deref())),
            "BRA" | "BRX" | "JMP" => Some(branch_op(inst, &pred)),
            "JMX" | "JMXU" if branch_target(inst).is_some() => Some(branch_op(inst, &pred)),
            "JMX" | "JMXU" => self.unsupported(inst, "indirect branch lifting is not implemented"),
            "CALL" | "CAL" | "JCAL" if self.has_local_call_site(inst) => {
                Some(self.local_call_op(inst))
            }
            "CALL" | "CAL" | "JCAL" => {
                self.unsupported(inst, "direct call target lifting is not implemented")
            }
            "BAR" => Some(format!("{}bar.sync 0;", pred)),
            "BSSY" => Some("// bssy reconvergence marker;".to_string()),
            "BSYNC" => Some("// bsync reconvergence marker;".to_string()),
            "DEPBAR" => Some("// depbar preserved from SASS;".to_string()),
            "MEMBAR" => Some(format!("{}membar.gl;", pred)),
            "NOP" => Some("// nop;".to_string()),
            "RET" if self.call_state.is_some() && is_local_subroutine_ret(inst) => {
                Some(self.local_return_op(inst))
            }
            "EXIT" | "RET" => Some(format!("{}ret;", pred)),
            "HMMA" | "IMMA" | "BMMA" | "DMMA" => {
                self.unsupported(inst, "tensor instruction lifting is not implemented")
            }
            "SHFL" if has_modifier(inst, "BFLY") => Some(shfl_bfly_op(inst, &pred)),
            "MUFU" if has_modifier(inst, "RSQ") => Some(mufu_rsq_op(inst, &pred)),
            "MUFU" if has_modifier(inst, "RCP") => Some(mufu_rcp_op(inst, &pred)),
            "MUFU" if has_modifier(inst, "LG2") => Some(mufu_unary_op(inst, &pred, "lg2")),
            "MUFU" if has_modifier(inst, "EX2") => Some(mufu_unary_op(inst, &pred, "ex2")),
            "MUFU" if has_modifier(inst, "SIN") => Some(mufu_unary_op(inst, &pred, "sin")),
            "MUFU" if has_modifier(inst, "COS") => Some(mufu_unary_op(inst, &pred, "cos")),
            "MUFU" => self.unsupported(inst, "MUFU sub-operation lifting is not implemented"),
            _ => self.unsupported(inst, "instruction lifting is not implemented"),
        }
    }

    fn has_local_call_site(&self, inst: &EnhancedSassInstruction) -> bool {
        self.call_state
            .as_ref()
            .is_some_and(|state| state.call_sites.contains_key(&inst.address))
    }

    fn local_call_op(&self, inst: &EnhancedSassInstruction) -> String {
        let state = self
            .call_state
            .as_ref()
            .expect("local call lowering requires call state");
        let site = state
            .call_sites
            .get(&inst.address)
            .expect("local call lowering requires a registered callsite");

        let mut lines = Vec::new();
        if let Some(skip_predicate) = inverted_predicate_prefix(inst) {
            lines.push(format!(
                "{}bra {};",
                skip_predicate, site.continuation_label
            ));
        }
        for (slot, stack_reg) in state.stack_regs.iter().enumerate() {
            lines.push(format!(
                "setp.eq.u32 {}, {}, {};",
                state.pred_reg, state.sp_reg, slot
            ));
            lines.push(format!(
                "@{} mov.u32 {}, {};",
                state.pred_reg, stack_reg, site.return_id
            ));
        }
        lines.push(format!("add.u32 {}, {}, 1;", state.sp_reg, state.sp_reg));
        lines.push(format!("bra {};", label_for_address(site.target)));

        let mut output = lines.join("\n    ");
        output.push('\n');
        output.push_str(&site.continuation_label);
        output.push(':');
        output
    }

    fn local_return_op(&self, inst: &EnhancedSassInstruction) -> String {
        let state = self
            .call_state
            .as_ref()
            .expect("local return lowering requires call state");
        let skip_label = inverted_predicate_prefix(inst)
            .map(|predicate| (predicate, format!("L_retskip_{:04x}", inst.address)));
        let mut lines = Vec::new();

        if let Some((skip_predicate, skip_label)) = &skip_label {
            lines.push(format!("{}bra {};", skip_predicate, skip_label));
        }
        lines.push(format!(
            "setp.eq.u32 {}, {}, 0;",
            state.pred_reg, state.sp_reg
        ));
        lines.push(format!("@{} ret;", state.pred_reg));
        lines.push(format!("sub.u32 {}, {}, 1;", state.sp_reg, state.sp_reg));
        lines.push(format!("mov.u32 {}, 0;", state.ret_pc_reg));
        for (slot, stack_reg) in state.stack_regs.iter().enumerate() {
            lines.push(format!(
                "setp.eq.u32 {}, {}, {};",
                state.pred_reg, state.sp_reg, slot
            ));
            lines.push(format!(
                "@{} mov.u32 {}, {};",
                state.pred_reg, state.ret_pc_reg, stack_reg
            ));
        }
        for site in state.call_sites.values() {
            lines.push(format!(
                "setp.eq.u32 {}, {}, {};",
                state.pred_reg, state.ret_pc_reg, site.return_id
            ));
            lines.push(format!(
                "@{} bra {};",
                state.pred_reg, site.continuation_label
            ));
        }
        lines.push("ret;".to_string());

        let mut output = lines.join("\n    ");
        if let Some((_, skip_label)) = skip_label {
            output.push('\n');
            output.push_str(&skip_label);
            output.push(':');
        }
        output
    }

    fn unsupported(&mut self, inst: &EnhancedSassInstruction, message: &str) -> Option<String> {
        self.diagnostics.push(SassLiftDiagnostic {
            address: Some(inst.address),
            opcode: inst.opcode.clone(),
            message: message.to_string(),
            instruction_text: inst.instruction_text.clone(),
        });
        if self.options.emit_unsupported_comments {
            Some(format!(
                "// unsupported SASS {} at 0x{:04x}: {}",
                inst.opcode, inst.address, message
            ))
        } else {
            None
        }
    }
}

fn needs_gpr_scratch(instructions: &[EnhancedSassInstruction]) -> bool {
    instructions.iter().any(|inst| {
        inst.opcode == "IADD3" && inst.src_operands.len() == 3
            || is_lop3_odd_predicate(inst)
            || is_lop3_predicate_lut(inst)
            || is_hadd2_zero_source(inst)
            || inst.opcode == "R2P"
            || is_shf_left_rotate(inst)
            || has_abs_float_operand(inst)
    })
}

fn needs_second_gpr_scratch(instructions: &[EnhancedSassInstruction]) -> bool {
    instructions
        .iter()
        .any(|inst| inst.opcode == "FSETP" && abs_setp_compare_operand_count(inst) > 1)
}

fn local_call_sites(instructions: &[EnhancedSassInstruction]) -> Vec<(u64, u64)> {
    instructions
        .iter()
        .filter(|inst| is_local_call(inst))
        .filter_map(|inst| branch_target(inst).map(|target| (inst.address, target)))
        .collect()
}

fn is_local_call(inst: &EnhancedSassInstruction) -> bool {
    matches!(inst.opcode.as_str(), "CALL" | "CAL" | "JCAL")
        && (has_modifier(inst, "REL") || branch_target(inst).is_some())
}

fn is_local_subroutine_ret(inst: &EnhancedSassInstruction) -> bool {
    inst.opcode == "RET"
        && (has_modifier(inst, "REL")
            || has_modifier(inst, "NODEC")
            || !inst.dest_operands.is_empty()
            || !inst.src_operands.is_empty())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct CudaParamDecl {
    index: u32,
    width: CudaParamWidth,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum CudaParamWidth {
    U32,
    U64,
}

impl CudaParamWidth {
    fn ptx_type(self) -> &'static str {
        match self {
            Self::U32 => "u32",
            Self::U64 => "u64",
        }
    }
}

fn cuda_param_decls(instructions: &[EnhancedSassInstruction]) -> Vec<CudaParamDecl> {
    let mut widths: BTreeMap<u32, CudaParamWidth> = BTreeMap::new();

    for inst in instructions {
        for operand in inst.src_operands.iter().chain(inst.dest_operands.iter()) {
            let Some((0, offset)) = constant_bank_operand(operand) else {
                continue;
            };
            let Some((index, byte_offset)) = cuda_param_index_and_byte_offset(offset) else {
                continue;
            };
            let width = if is_64bit_modifier(inst) || byte_offset >= 4 {
                CudaParamWidth::U64
            } else {
                CudaParamWidth::U32
            };
            widths
                .entry(index)
                .and_modify(|existing| *existing = (*existing).max(width))
                .or_insert(width);
        }
    }

    let Some(max_index) = widths.keys().next_back().copied() else {
        return Vec::new();
    };

    (0..=max_index)
        .map(|index| CudaParamDecl {
            index,
            width: widths.get(&index).copied().unwrap_or(CudaParamWidth::U64),
        })
        .collect()
}

fn cuda_param_name(index: u32, _width: CudaParamWidth) -> String {
    match index {
        0 => "out".to_string(),
        1 => "in".to_string(),
        _ => format!("param{}", index),
    }
}

#[derive(Debug, Default)]
struct RegisterDecls {
    max_gpr: u32,
    max_pred: u32,
    max_uniform_gpr: u32,
    max_uniform_pred: u32,
    max_b64: u32,
}

impl RegisterDecls {
    fn from_instructions(instructions: &[EnhancedSassInstruction]) -> Self {
        let mut decls = Self::default();
        for inst in instructions {
            for operand in inst.dest_operands.iter().chain(inst.src_operands.iter()) {
                collect_register_decl(operand, &mut decls);
            }
            if let Some(predicate) = &inst.predicate {
                collect_register_decl(predicate, &mut decls);
            }
            collect_implicit_register_pair_decl(inst, &mut decls);
            collect_implicit_r2p_predicate_decl(inst, &mut decls);
        }
        decls
    }

    fn has_decls(&self) -> bool {
        self.max_gpr > 0
            || self.max_pred > 0
            || self.max_uniform_gpr > 0
            || self.max_uniform_pred > 0
            || self.max_b64 > 0
    }
}

fn collect_implicit_register_pair_decl(inst: &EnhancedSassInstruction, decls: &mut RegisterDecls) {
    if !is_64bit_modifier(inst) {
        return;
    }
    for operand in inst.dest_operands.iter().chain(inst.src_operands.iter()) {
        let SassOperand::Register(reg) = operand else {
            continue;
        };
        if reg.is_zero {
            continue;
        }
        match reg.prefix.as_str() {
            "R" => decls.max_gpr = decls.max_gpr.max(reg.number + 2),
            "UR" => decls.max_uniform_gpr = decls.max_uniform_gpr.max(reg.number + 2),
            _ => {}
        }
    }
}

fn collect_implicit_r2p_predicate_decl(inst: &EnhancedSassInstruction, decls: &mut RegisterDecls) {
    if inst.opcode != "R2P" {
        return;
    }
    let Some(mask) = r2p_mask(inst) else {
        return;
    };
    if mask == 0 {
        return;
    }
    let highest_bit = u32::BITS - 1 - mask.leading_zeros();
    decls.max_pred = decls.max_pred.max(highest_bit + 1);
}

fn collect_register_decl(operand: &SassOperand, decls: &mut RegisterDecls) {
    match operand {
        SassOperand::Register(reg) => collect_register(reg, decls),
        SassOperand::Predicate { register, .. } => collect_register(register, decls),
        SassOperand::Memory { base, index, .. } => {
            if let Some(reg) = base {
                collect_register(reg, decls);
            }
            if let Some(reg) = index {
                collect_register(reg, decls);
            }
        }
        _ => {}
    }
}

fn collect_register(reg: &SassRegister, decls: &mut RegisterDecls) {
    if reg.is_zero {
        return;
    }
    match reg.prefix.as_str() {
        "R" => decls.max_gpr = decls.max_gpr.max(reg.number + 1),
        "P" => decls.max_pred = decls.max_pred.max(reg.number + 1),
        "UR" => decls.max_uniform_gpr = decls.max_uniform_gpr.max(reg.number + 1),
        "UP" => decls.max_uniform_pred = decls.max_uniform_pred.max(reg.number + 1),
        _ => {}
    }
}

fn ptx_version_for_sm(sm_version: u32) -> &'static str {
    if sm_version >= 120 {
        ".version 8.7"
    } else {
        ".version 8.5"
    }
}

fn unary_op(inst: &EnhancedSassInstruction, pred: &str, op: &str, ty: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let src = inst
        .src_operands
        .first()
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    format!("{}{}.{} {}, {};", pred, op, ty, dst, src)
}

fn binary_op(inst: &EnhancedSassInstruction, pred: &str, op: &str, ty: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let src0 = inst
        .src_operands
        .first()
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    let src1 = inst
        .src_operands
        .get(1)
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    format!("{}{}.{} {}, {}, {};", pred, op, ty, dst, src0, src1)
}

// SASS R/UR registers are 32-bit physical words. A .64 operand consumes two
// adjacent words; PTX %rdN is a separate virtual register, not an alias.
fn pair_read(operand: &SassOperand, pred: &str, ur_scratch: &str) -> Option<(String, String)> {
    match operand {
        SassOperand::Register(reg) if reg.is_zero => Some((String::new(), "0".to_string())),
        SassOperand::Register(reg) if reg.prefix == "R" => {
            let wide = format!("%rd{}", reg.number);
            Some((format!("{}mov.b64 {}, {{%r{}, %r{}}};", pred, wide,
                          reg.number, reg.number + 1), wide))
        }
        SassOperand::Register(reg) if reg.prefix == "UR" => Some((
            format!("{}mov.b64 {}, {{%ur{}, %ur{}}};", pred, ur_scratch,
                    reg.number, reg.number + 1),
            ur_scratch.to_string(),
        )),
        _ => None,
    }
}

fn pair_write(operand: &SassOperand, pred: &str) -> Option<String> {
    match operand {
        SassOperand::Register(reg) if reg.prefix == "R" && !reg.is_zero => Some(format!(
            "{}mov.b64 {{%r{}, %r{}}}, %rd{};",
            pred, reg.number, reg.number + 1, reg.number
        )),
        _ => None,
    }
}

fn iadd_64_op(inst: &EnhancedSassInstruction, pred: &str, ur_scratch: &str) -> Option<String> {
    if inst.modifiers.len() != 1 || !has_modifier(inst, "64")
        || inst.dest_operands.len() != 1 || inst.src_operands.len() != 2
        || !matches!(inst.dest_operands.first()?, SassOperand::Register(reg)
            if reg.prefix == "R" && !reg.is_zero)
        || !matches!(inst.src_operands.first()?, SassOperand::Register(reg)
            if reg.prefix == "R" && !reg.is_zero)
        || !matches!(inst.src_operands.get(1)?, SassOperand::Register(reg)
            if (reg.prefix == "R" || reg.prefix == "UR") && !reg.is_zero) {
        return None;
    }
    let dst = inst.dest_operands.first()?;
    if inst.src_operands.iter().take(2).filter(|operand| matches!(operand,
        SassOperand::Register(reg) if reg.prefix == "UR" && !reg.is_zero)).count() > 1 {
        return None;
    }
    let (setup0, src0) = pair_read(inst.src_operands.first()?, pred, ur_scratch)?;
    let (setup1, src1) = pair_read(inst.src_operands.get(1)?, pred, ur_scratch)?;
    let output = pair_write(dst, pred)?;
    let wide_dst = dest_rd_operand(inst)?;
    Some(format!("{}\n    {}\n    {}add.u64 {}, {}, {};\n    {}",
                 setup0, setup1, pred, wide_dst, src0, src1, output))
}

fn cs2r_reads_srz(inst: &EnhancedSassInstruction) -> bool {
    matches!(inst.src_operands.first(),
        Some(SassOperand::Label(name) | SassOperand::SpecialRegister(name))
        if matches!(name.as_str(), "SRZ" | "SR_Z"))
}

fn cs2r_srz_pair_form(inst: &EnhancedSassInstruction) -> bool {
    inst.modifiers.is_empty()
        && inst.dest_operands.len() == 1
        && inst.src_operands.len() == 1
        && cs2r_reads_srz(inst)
        && matches!(inst.dest_operands.first(), Some(SassOperand::Register(reg))
            if reg.prefix == "R" && !reg.is_zero && reg.number % 2 == 0)
}

fn supported_imad_wide_pair_form(inst: &EnhancedSassInstruction) -> bool {
    inst.modifiers.len() == 2 && has_modifier(inst, "WIDE") && has_modifier(inst, "U32")
        && inst.dest_operands.len() == 1 && inst.src_operands.len() == 3
        && matches!(inst.dest_operands.first(), Some(SassOperand::Register(reg))
            if reg.prefix == "R" && !reg.is_zero)
        && matches!(inst.src_operands.first(), Some(SassOperand::Register(reg))
            if reg.prefix == "R" && !reg.is_zero)
        && matches!(inst.src_operands.get(1), Some(SassOperand::Register(reg))
            if reg.prefix == "UR" && !reg.is_zero)
        && matches!(inst.src_operands.get(2), Some(SassOperand::Register(reg))
            if reg.prefix == "R" && !reg.is_zero)
}

fn float_binary_op(
    inst: &EnhancedSassInstruction,
    pred: &str,
    op: &str,
    ty: &str,
    scratch_gpr: Option<&str>,
) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let raw_src0 = inst
        .src_operands
        .first()
        .map(format_float_operand)
        .unwrap_or_else(|| format_f32_literal(0.0));
    let raw_src1 = inst
        .src_operands
        .get(1)
        .map(format_float_operand)
        .unwrap_or_else(|| format_f32_literal(0.0));
    let abs_src0 = inst.src_operands.first().and_then(abs_float_operand);
    let abs_src1 = inst.src_operands.get(1).and_then(abs_float_operand);
    let src0 = abs_src0.clone().unwrap_or(raw_src0);
    let src1 = abs_src1.clone().unwrap_or(raw_src1);
    match (abs_src0, abs_src1) {
        (Some(abs0), Some(abs1)) => {
            let scratch = scratch_gpr.unwrap_or(&dst);
            format!(
                "{}abs.f32 {}, {};\n    {}abs.f32 {}, {};\n    {}{}.{} {}, {}, {};",
                pred, scratch, abs0, pred, dst, abs1, pred, op, ty, dst, scratch, dst
            )
        }
        (Some(abs0), None) => {
            let scratch = scratch_gpr.unwrap_or(&dst);
            format!(
                "{}abs.f32 {}, {};\n    {}{}.{} {}, {}, {};",
                pred, scratch, abs0, pred, op, ty, dst, scratch, src1
            )
        }
        (None, Some(abs1)) => {
            let scratch = scratch_gpr.unwrap_or(&dst);
            format!(
                "{}abs.f32 {}, {};\n    {}{}.{} {}, {}, {};",
                pred, scratch, abs1, pred, op, ty, dst, src0, scratch
            )
        }
        (None, None) => format!("{}{}.{} {}, {}, {};", pred, op, ty, dst, src0, src1),
    }
}

fn iadd3_op(
    inst: &EnhancedSassInstruction,
    pred: &str,
    ty: &str,
    scratch_gpr: Option<&str>,
) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let values: Vec<String> = inst
        .src_operands
        .iter()
        .filter_map(format_integer_data_operand)
        .take(3)
        .collect();
    let src0 = values.first().cloned().unwrap_or_else(|| "0".to_string());
    let src1 = values.get(1).cloned().unwrap_or_else(|| "0".to_string());
    let src2 = values.get(2).cloned().unwrap_or_else(|| "0".to_string());
    let scratch = scratch_gpr.unwrap_or("%r0");
    format!(
        "{}add.{} {}, {}, {};\n    {}add.{} {}, {}, {};",
        pred, ty, scratch, src0, src1, pred, ty, dst, scratch, src2
    )
}

fn imad_op(inst: &EnhancedSassInstruction, pred: &str, ty: &str, mode: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let values: Vec<String> = inst
        .src_operands
        .iter()
        .filter_map(format_integer_data_operand)
        .take(3)
        .collect();
    let src0 = values.first().cloned().unwrap_or_else(|| "0".to_string());
    let src1 = values.get(1).cloned().unwrap_or_else(|| "0".to_string());
    let src2 = values.get(2).cloned().unwrap_or_else(|| "0".to_string());
    format!(
        "{}mad.{}.{} {}, {}, {}, {};",
        pred, mode, ty, dst, src0, src1, src2
    )
}

fn ternary_op(inst: &EnhancedSassInstruction, pred: &str, op: &str, ty: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let src0 = inst
        .src_operands
        .first()
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    let src1 = inst
        .src_operands
        .get(1)
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    let src2 = inst
        .src_operands
        .get(2)
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    format!(
        "{}{}.{} {}, {}, {}, {};",
        pred, op, ty, dst, src0, src1, src2
    )
}

fn uiadd3_op(inst: &EnhancedSassInstruction, pred: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%ur0".to_string());
    let values: Vec<String> = inst
        .src_operands
        .iter()
        .filter_map(format_integer_data_operand)
        .take(3)
        .collect();
    let src0 = values.first().cloned().unwrap_or_else(|| "0".to_string());
    let src1 = values.get(1).cloned().unwrap_or_else(|| "0".to_string());
    let src2 = values.get(2).cloned().unwrap_or_else(|| "0".to_string());
    let values = [src0, src1, src2];
    let first_idx = values.iter().position(|src| src == &dst).unwrap_or(0);
    let second_idx = if first_idx == 0 { 1 } else { 0 };
    let third_idx = (0..3)
        .find(|idx| *idx != first_idx && *idx != second_idx)
        .unwrap_or(2);

    format!(
        "{}add.u32 {}, {}, {};\n    {}add.u32 {}, {}, {};",
        pred, dst, values[first_idx], values[second_idx], pred, dst, dst, values[third_idx]
    )
}

fn float_ternary_op(inst: &EnhancedSassInstruction, pred: &str, op: &str, ty: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let src0 = inst
        .src_operands
        .first()
        .map(format_float_operand)
        .unwrap_or_else(|| format_f32_literal(0.0));
    let src1 = inst
        .src_operands
        .get(1)
        .map(format_float_operand)
        .unwrap_or_else(|| format_f32_literal(0.0));
    let src2 = inst
        .src_operands
        .get(2)
        .map(format_float_operand)
        .unwrap_or_else(|| format_f32_literal(0.0));
    format!(
        "{}{}.{} {}, {}, {}, {};",
        pred, op, ty, dst, src0, src1, src2
    )
}

fn hfma2_constant_op(inst: &EnhancedSassInstruction, pred: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let bits = extract_sass_encoding(inst)
        .map(|encoding| (encoding >> 32) as u32)
        .unwrap_or(0);
    format!("{}mov.b32 {}, 0x{:08x};", pred, dst, bits)
}

fn f2fp_pack_ab_op(inst: &EnhancedSassInstruction, pred: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let src0 = inst
        .src_operands
        .first()
        .map(format_f32_pack_operand)
        .unwrap_or_else(|| "0f00000000".to_string());
    let src1 = inst
        .src_operands
        .get(1)
        .map(format_f32_pack_operand)
        .unwrap_or_else(|| "0f00000000".to_string());
    let packed_type = if has_modifier(inst, "BF16") {
        "bf16x2"
    } else {
        "f16x2"
    };
    format!(
        "{}cvt.rn.{}.f32 {}, {}, {};",
        pred, packed_type, dst, src0, src1
    )
}

fn hadd2_op(inst: &EnhancedSassInstruction, pred: &str, scratch_gpr: Option<&str>) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let src0_is_zero = inst
        .src_operands
        .first()
        .map(is_half2_zero_operand)
        .unwrap_or(true);
    let src1_is_zero = inst
        .src_operands
        .get(1)
        .map(is_half2_zero_operand)
        .unwrap_or(true);
    let scratch = scratch_gpr.unwrap_or("%r0");
    let src0 = inst
        .src_operands
        .first()
        .map(|operand| format_half2_operand(operand, scratch))
        .unwrap_or_else(|| scratch.to_string());
    let src1 = inst
        .src_operands
        .get(1)
        .map(|operand| format_half2_operand(operand, scratch))
        .unwrap_or_else(|| scratch.to_string());
    let add = format!("{}add.rn.f16x2 {}, {}, {};", pred, dst, src0, src1);
    if src0_is_zero || src1_is_zero {
        format!("{}mov.b32 {}, 0;\n    {}", pred, scratch, add)
    } else {
        add
    }
}

fn hmul2_op(inst: &EnhancedSassInstruction, pred: &str, scratch_gpr: Option<&str>) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let src0_is_zero = inst
        .src_operands
        .first()
        .map(is_half2_zero_operand)
        .unwrap_or(true);
    let src1_is_zero = inst
        .src_operands
        .get(1)
        .map(is_half2_zero_operand)
        .unwrap_or(true);
    let scratch = scratch_gpr.unwrap_or("%r0");
    let src0 = inst
        .src_operands
        .first()
        .map(|operand| format_half2_operand(operand, scratch))
        .unwrap_or_else(|| scratch.to_string());
    let src1 = inst
        .src_operands
        .get(1)
        .map(|operand| format_half2_operand(operand, scratch))
        .unwrap_or_else(|| scratch.to_string());
    let mul = format!("{}mul.rn.f16x2 {}, {}, {};", pred, dst, src0, src1);
    if src0_is_zero || src1_is_zero {
        format!("{}mov.b32 {}, 0;\n    {}", pred, scratch, mul)
    } else {
        mul
    }
}

fn i2fp_op(inst: &EnhancedSassInstruction, pred: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let rounding = if has_modifier(inst, "RP") {
        "rp"
    } else if has_modifier(inst, "RM") {
        "rm"
    } else if has_modifier(inst, "RZ") {
        "rz"
    } else {
        "rn"
    };
    let dst_ty = if has_modifier(inst, "F64") {
        "f64"
    } else if has_modifier(inst, "F16") {
        "f16"
    } else {
        "f32"
    };
    let src_ty = if has_modifier(inst, "U64") {
        "u64"
    } else if has_modifier(inst, "S64") {
        "s64"
    } else if has_modifier(inst, "U32") {
        "u32"
    } else {
        "s32"
    };
    let src = inst
        .src_operands
        .first()
        .map(|operand| {
            if matches!(src_ty, "u64" | "s64") {
                match operand {
                    SassOperand::Register(reg) if reg.prefix == "UR" && !reg.is_zero => {
                        format!("%rd{}", reg.number)
                    }
                    _ => format_rd_operand(operand),
                }
            } else {
                format_operand(operand)
            }
        })
        .unwrap_or_else(|| "0".to_string());
    let src_setup = inst
        .src_operands
        .first()
        .and_then(|operand| {
            if !matches!(src_ty, "u64" | "s64") {
                return None;
            }
            match operand {
                SassOperand::Register(reg) if reg.prefix == "UR" && !reg.is_zero => {
                    let (setup, _) =
                        wide_base_operand_setup(&format!("%rd{}", reg.number), operand, pred);
                    Some(setup)
                }
                _ => None,
            }
        })
        .unwrap_or_default();
    if !src_setup.is_empty() {
        return format!(
            "{}\n    {}cvt.{}.{}.{} {}, {};",
            src_setup, pred, rounding, dst_ty, src_ty, dst, src
        );
    }
    format!(
        "{}cvt.{}.{}.{} {}, {};",
        pred, rounding, dst_ty, src_ty, dst, src
    )
}

fn f2i_op(inst: &EnhancedSassInstruction, pred: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let src = inst
        .src_operands
        .first()
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    format!("{}cvt.rzi.u32.f32 {}, {};", pred, dst, src)
}

fn frnd_trunc_op(inst: &EnhancedSassInstruction, pred: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let src = inst
        .src_operands
        .first()
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    format!("{}cvt.rzi.f32.f32 {}, {};", pred, dst, src)
}

fn mufu_rsq_op(inst: &EnhancedSassInstruction, pred: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let src = inst
        .src_operands
        .first()
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    format!("{}rsqrt.approx.ftz.f32 {}, {};", pred, dst, src)
}

fn mufu_rcp_op(inst: &EnhancedSassInstruction, pred: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let src = inst
        .src_operands
        .first()
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    format!("{}rcp.approx.ftz.f32 {}, {};", pred, dst, src)
}

fn mufu_unary_op(inst: &EnhancedSassInstruction, pred: &str, op: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let src = inst
        .src_operands
        .first()
        .map(format_float_operand)
        .unwrap_or_else(|| format_f32_literal(0.0));
    format!("{}{}.approx.ftz.f32 {}, {};", pred, op, dst, src)
}

fn r2p_op(inst: &EnhancedSassInstruction, pred: &str, scratch_gpr: Option<&str>) -> String {
    let src = inst
        .src_operands
        .first()
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    let mask = r2p_mask(inst).unwrap_or(0);
    let scratch = scratch_gpr.unwrap_or("%r0");
    let mut lines = Vec::new();
    for bit in 0..8 {
        let bit_mask = 1u32 << bit;
        if mask & bit_mask == 0 {
            continue;
        }
        lines.push(format!(
            "{}and.b32 {}, {}, 0x{:x};\n    {}setp.ne.u32 %p{}, {}, 0;",
            pred, scratch, src, bit_mask, pred, bit, scratch
        ));
    }
    if lines.is_empty() {
        format!("{}// r2p mask empty;", pred)
    } else {
        lines.join("\n    ")
    }
}

fn r2p_mask(inst: &EnhancedSassInstruction) -> Option<u32> {
    match inst.src_operands.get(1)? {
        SassOperand::Immediate(value) if *value >= 0 => Some(*value as u32),
        _ => None,
    }
}

fn fmnmx_op(inst: &EnhancedSassInstruction, pred: &str, scratch_gpr: Option<&str>) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let op = fmnmx_operator(inst);
    let raw_src0 = inst
        .src_operands
        .first()
        .map(format_float_operand)
        .unwrap_or_else(|| format_f32_literal(0.0));
    let raw_src1 = inst
        .src_operands
        .get(1)
        .map(format_float_operand)
        .unwrap_or_else(|| format_f32_literal(0.0));
    let abs_src0 = inst.src_operands.first().and_then(abs_float_operand);
    let abs_src1 = inst.src_operands.get(1).and_then(abs_float_operand);
    let src0 = abs_src0.clone().unwrap_or(raw_src0);
    let src1 = abs_src1.clone().unwrap_or(raw_src1);
    match (abs_src0, abs_src1) {
        (Some(abs0), Some(abs1)) => {
            let scratch = scratch_gpr.unwrap_or(&dst);
            format!(
                "{}abs.f32 {}, {};\n    {}abs.f32 {}, {};\n    {}{}.f32 {}, {}, {};",
                pred, scratch, abs0, pred, dst, abs1, pred, op, dst, scratch, dst
            )
        }
        (Some(abs0), None) => {
            let scratch = scratch_gpr.unwrap_or(&dst);
            format!(
                "{}abs.f32 {}, {};\n    {}{}.f32 {}, {}, {};",
                pred, scratch, abs0, pred, op, dst, scratch, src1
            )
        }
        (None, Some(abs1)) => {
            let scratch = scratch_gpr.unwrap_or(&dst);
            format!(
                "{}abs.f32 {}, {};\n    {}{}.f32 {}, {}, {};",
                pred, scratch, abs1, pred, op, dst, src0, scratch
            )
        }
        (None, None) => format!("{}{}.f32 {}, {}, {};", pred, op, dst, src0, src1),
    }
}

fn imad_wide_op(inst: &EnhancedSassInstruction, pred: &str, product_scratch: &str) -> String {
    let dst = dest_rd_operand(inst).unwrap_or_else(|| "%rd0".to_string());
    let data_operands: Vec<&SassOperand> = inst
        .src_operands
        .iter()
        .filter(|operand| format_integer_data_operand(operand).is_some())
        .take(3)
        .collect();
    let src0 = data_operands
        .first()
        .map(|operand| format_operand(operand))
        .unwrap_or_else(|| "0".to_string());
    let src1 = data_operands
        .get(1)
        .map(|operand| format_operand(operand))
        .unwrap_or_else(|| "0".to_string());
    let (base_setup, base) = data_operands
        .get(2)
        .map(|operand| wide_base_operand_setup(&dst, operand, pred))
        .unwrap_or_else(|| (String::new(), "0".to_string()));

    let mut lines = format!("{}mul.wide.u32 {}, {}, {};", pred, product_scratch, src0, src1);
    if !base_setup.is_empty() {
        lines.push_str("\n    ");
        lines.push_str(&base_setup);
    }
    lines.push_str(&format!("\n    {}add.u64 {}, {}, {};", pred, dst, base, product_scratch));
    if let Some(unpack) = inst.dest_operands.first().and_then(|operand| pair_write(operand, pred)) {
        lines.push_str("\n    ");
        lines.push_str(&unpack);
    }
    lines
}

fn wide_base_operand_setup(dst: &str, operand: &SassOperand, pred: &str) -> (String, String) {
    match operand {
        SassOperand::Register(reg) if reg.prefix == "UR" && !reg.is_zero => {
            (
                format!("{}mov.b64 {}, {{%ur{}, %ur{}}};", pred, dst,
                        reg.number, reg.number + 1),
                dst.to_string(),
            )
        }
        SassOperand::Register(reg) if reg.prefix == "R" && !reg.is_zero => {
            let wide = format!("%rd{}", reg.number);
            (format!("{}mov.b64 {}, {{%r{}, %r{}}};", pred, wide,
                     reg.number, reg.number + 1), wide)
        }
        _ => (String::new(), format_rd_operand(operand)),
    }
}

fn iadd3_extended_op(inst: &EnhancedSassInstruction, pred: &str, ty: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let terms: Vec<(String, bool)> = inst
        .src_operands
        .iter()
        .filter(|op| !is_zero_register_operand(op) && !is_predicate_operand(op))
        .map(format_signed_operand)
        .collect();

    match terms.as_slice() {
        [] => format!("{}mov.u32 {}, 0;", pred, dst),
        [(src, false)] => format!("{}mov.u32 {}, {};", pred, dst, src),
        [(src, true)] => format!("{}sub.{} {}, 0, {};", pred, ty, dst, src),
        [(src0, false), (src1, false)] => {
            format!("{}add.{} {}, {}, {};", pred, ty, dst, src0, src1)
        }
        [(src0, false), (src1, true)] => {
            format!("{}sub.{} {}, {}, {};", pred, ty, dst, src0, src1)
        }
        [(src0, true), (src1, false)] => {
            format!("{}sub.{} {}, {}, {};", pred, ty, dst, src1, src0)
        }
        [(src0, true), (src1, true)] => format!(
            "{}sub.{} {}, 0, {};\n    {}sub.{} {}, {}, {};",
            pred, ty, dst, src0, pred, ty, dst, dst, src1
        ),
        [(src0, neg0), rest @ ..] => {
            let mut lines = if *neg0 {
                format!("{}sub.{} {}, 0, {};", pred, ty, dst, src0)
            } else {
                format!("{}mov.u32 {}, {};", pred, dst, src0)
            };
            for (src, negated) in rest {
                let op = if *negated { "sub" } else { "add" };
                lines.push_str(&format!(
                    "\n    {}{}.{} {}, {}, {};",
                    pred, op, ty, dst, dst, src
                ));
            }
            lines
        }
    }
}

fn lop3_xor_op(inst: &EnhancedSassInstruction, pred: &str) -> String {
    lop3_binary_op(inst, pred, "xor")
}

fn lop3_lut_op(inst: &EnhancedSassInstruction, pred: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let src0 = inst
        .src_operands
        .first()
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    let src1 = inst
        .src_operands
        .get(1)
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    let src2 = inst
        .src_operands
        .get(2)
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    let lut = match inst.src_operands.get(3) {
        Some(SassOperand::Immediate(value)) => value & 0xff,
        _ => 0,
    };
    format!(
        "{}lop3.b32 {}, {}, {}, {}, 0x{:02x};",
        pred, dst, src0, src1, src2, lut
    )
}

fn lop3_predicate_lut_op(
    inst: &EnhancedSassInstruction,
    pred: &str,
    scratch_gpr: Option<&str>,
) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%p0".to_string());
    let scratch = scratch_gpr.unwrap_or("%r0");
    let src0 = inst
        .src_operands
        .first()
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    let src1 = inst
        .src_operands
        .get(1)
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    let src2 = inst
        .src_operands
        .get(2)
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    let lut = match inst.src_operands.get(4) {
        Some(SassOperand::Immediate(value)) => value & 0xff,
        _ => 0,
    };
    format!(
        "{}lop3.b32 {}, {}, {}, {}, 0x{:02x};\n    {}setp.ne.u32 {}, {}, 0;",
        pred, scratch, src0, src1, src2, lut, pred, dst, scratch
    )
}

fn plop3_binary_lut_op(inst: &EnhancedSassInstruction, pred: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%p0".to_string());
    let src0 = inst
        .src_operands
        .get(1)
        .map(format_predicate_logic_operand)
        .unwrap_or_else(|| "%p0".to_string());
    let src1 = inst
        .src_operands
        .get(2)
        .map(format_predicate_logic_operand)
        .unwrap_or_else(|| "%p0".to_string());
    let op = plop3_binary_lut_operator(inst).unwrap_or("or");
    format!("{}{}.pred {}, {}, {};", pred, op, dst, src0, src1)
}

fn lop_binary_op(inst: &EnhancedSassInstruction, pred: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let mut srcs = inst
        .src_operands
        .iter()
        .filter_map(format_integer_data_operand);
    let src0 = srcs.next().unwrap_or_else(|| "0".to_string());
    let src1 = srcs.next().unwrap_or_else(|| "0".to_string());
    let op = lop_binary_operator(inst).unwrap_or("and");
    format!("{}{}.b32 {}, {}, {};", pred, op, dst, src0, src1)
}

fn lop3_binary_op(inst: &EnhancedSassInstruction, pred: &str, op: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let src0 = inst
        .src_operands
        .first()
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    let src1 = inst
        .src_operands
        .get(1)
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    format!("{}{}.b32 {}, {}, {};", pred, op, dst, src0, src1)
}

fn lop3_odd_predicate_op(
    inst: &EnhancedSassInstruction,
    pred: &str,
    scratch_gpr: Option<&str>,
) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%p0".to_string());
    let value = inst
        .src_operands
        .get(1)
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    let scratch = scratch_gpr.unwrap_or("%r0");
    format!(
        "{}and.b32 {}, {}, 1;\n    {}setp.ne.u32 {}, {}, 0;",
        pred, scratch, value, pred, dst, scratch
    )
}

fn shf_op(inst: &EnhancedSassInstruction, pred: &str, scratch_gpr: Option<&str>) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let amount = inst
        .src_operands
        .get(1)
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    if has_modifier(inst, "R") {
        let src0 = inst
            .src_operands
            .first()
            .map(format_operand)
            .unwrap_or_else(|| "0".to_string());
        let src1 = inst
            .src_operands
            .get(2)
            .map(format_operand)
            .unwrap_or_else(|| "0".to_string());
        if has_modifier(inst, "U64") || has_modifier(inst, "S64") {
            format!(
                "{}shf.r.clamp.b32 {}, {}, {}, {};",
                pred, dst, src0, src1, amount
            )
        } else {
            format!(
                "{}shr.{} {}, {}, {};",
                pred,
                data_type_suffix(inst, SassDataType::U32),
                dst,
                src1,
                amount
            )
        }
    } else if is_shf_left_rotate(inst) {
        let value = inst
            .src_operands
            .first()
            .map(format_operand)
            .unwrap_or_else(|| "0".to_string());
        let scratch = scratch_gpr.unwrap_or("%r0");
        let right_amount = shift_amount_immediate(inst)
            .map(|amount| 32 - amount)
            .unwrap_or(0);
        format!(
            "{}shl.b32 {}, {}, {};\n    {}shr.u32 {}, {}, {};\n    {}or.b32 {}, {}, {};",
            pred, dst, value, amount, pred, scratch, value, right_amount, pred, dst, dst, scratch
        )
    } else {
        let value = inst
            .src_operands
            .first()
            .map(format_operand)
            .unwrap_or_else(|| "0".to_string());
        format!("{}shl.b32 {}, {}, {};", pred, dst, value, amount)
    }
}

fn ushf_op(inst: &EnhancedSassInstruction, pred: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%ur0".to_string());
    let amount = inst
        .src_operands
        .get(1)
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    let src0 = inst
        .src_operands
        .first()
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    if has_modifier(inst, "R") {
        let src1 = inst
            .src_operands
            .get(2)
            .map(format_operand)
            .unwrap_or_else(|| "0".to_string());
        format!(
            "{}shf.r.clamp.b32 {}, {}, {}, {};",
            pred, dst, src0, src1, amount
        )
    } else {
        format!("{}shl.b32 {}, {}, {};", pred, dst, src0, amount)
    }
}

fn vimnmx_op(inst: &EnhancedSassInstruction, pred: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let src0 = inst
        .src_operands
        .first()
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    let src1 = inst
        .src_operands
        .get(1)
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    let op = vimnmx_static_operator(inst).unwrap_or("min");
    format!(
        "{}{}.{} {}, {}, {};",
        pred,
        op,
        data_type_suffix(inst, SassDataType::S32),
        dst,
        src0,
        src1
    )
}

fn shfl_bfly_op(inst: &EnhancedSassInstruction, pred: &str) -> String {
    let data_dst = if inst
        .dest_operands
        .first()
        .map(is_pt_register_operand)
        .unwrap_or(false)
    {
        inst.src_operands
            .first()
            .map(format_operand)
            .unwrap_or_else(|| "%r0".to_string())
    } else {
        dest_operand(inst).unwrap_or_else(|| "%r0".to_string())
    };
    let src = inst
        .src_operands
        .get(1)
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    let lane = inst
        .src_operands
        .get(2)
        .map(format_hex_immediate_operand)
        .unwrap_or_else(|| "0".to_string());
    let mask = inst
        .src_operands
        .get(3)
        .map(format_hex_immediate_operand)
        .unwrap_or_else(|| "0x1f".to_string());
    format!(
        "{}shfl.sync.bfly.b32 {}, {}, {}, {}, 0xffffffff;",
        pred, data_dst, src, lane, mask
    )
}

fn sel_op(inst: &EnhancedSassInstruction, pred: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let mut src0 = inst
        .src_operands
        .first()
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    let mut src1 = inst
        .src_operands
        .get(1)
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    let (predicate, negated) = inst
        .src_operands
        .get(2)
        .map(format_sel_predicate_operand)
        .unwrap_or_else(|| ("%p0".to_string(), false));
    if negated {
        std::mem::swap(&mut src0, &mut src1);
    }
    format!(
        "{}selp.{} {}, {}, {}, {};",
        pred,
        data_type_suffix(inst, SassDataType::U32),
        dst,
        src0,
        src1,
        predicate
    )
}

fn usel_op(inst: &EnhancedSassInstruction, pred: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%ur0".to_string());
    let mut src0 = inst
        .src_operands
        .first()
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    let mut src1 = inst
        .src_operands
        .get(1)
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    let (predicate, negated) = inst
        .src_operands
        .get(2)
        .map(format_sel_predicate_operand)
        .unwrap_or_else(|| ("%up0".to_string(), false));
    if negated {
        std::mem::swap(&mut src0, &mut src1);
    }
    if is_64bit_modifier(inst) {
        let high_dst = uniform_high_register_name(&dst).unwrap_or_else(|| dst.clone());
        let high_src0 = uniform_high_register_name(&src0).unwrap_or_else(|| src0.clone());
        let high_src1 = uniform_high_register_name(&src1).unwrap_or_else(|| src1.clone());
        return format!(
            "{}selp.u32 {}, {}, {}, {};\n    {}selp.u32 {}, {}, {}, {};",
            pred, dst, src0, src1, predicate, pred, high_dst, high_src0, high_src1, predicate
        );
    }
    let ty = data_type_suffix(inst, SassDataType::U32);
    format!(
        "{}selp.{} {}, {}, {}, {};",
        pred, ty, dst, src0, src1, predicate
    )
}

fn uniform_high_register_name(name: &str) -> Option<String> {
    let number = name.strip_prefix("%ur")?.parse::<u32>().ok()?;
    Some(format!("%ur{}", number + 1))
}

fn fsel_op(inst: &EnhancedSassInstruction, pred: &str) -> Option<String> {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let mut src0 = match inst.src_operands.first() {
        Some(source) => format_fsel_source(inst, 0, source)?,
        None => "0".to_string(),
    };
    let mut src1 = match inst.src_operands.get(1) {
        Some(source) => format_fsel_source(inst, 1, source)?,
        None => "0".to_string(),
    };
    let (predicate, negated) = inst
        .src_operands
        .get(2)
        .map(format_sel_predicate_operand)
        .unwrap_or_else(|| ("%p0".to_string(), false));
    if negated {
        std::mem::swap(&mut src0, &mut src1);
    }
    Some(format!(
        "{}selp.b32 {}, {}, {}, {};",
        pred, dst, src0, src1, predicate
    ))
}

fn format_fsel_source(
    inst: &EnhancedSassInstruction,
    index: usize,
    source: &SassOperand,
) -> Option<String> {
    if matches!(source, SassOperand::Label(label) if label == "-QNAN") {
        // cuobjdump prints different negative qNaN payloads identically as
        // -QNAN. The encoded immediate, not that display label, identifies
        // the exact 32-bit value selected by this FSEL instruction.
        let encoding = extract_sass_encoding(inst)?;
        // Both independently assembled FSEL immediate references have low
        // 16 bits 0x7808; register-source FSEL instead has 0x7208.
        if index != 1 || encoding & 0xffff != 0x7808 {
            return None;
        }
        let bits = (encoding >> 32) as u32;
        if bits & 0xffc0_0000 != 0xffc0_0000 {
            return None;
        }
        return Some(format!("0x{:08x}", bits));
    }
    Some(format_operand(source))
}

fn idp_4a_s8_s8_op(inst: &EnhancedSassInstruction, pred: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let src0 = inst
        .src_operands
        .first()
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    let src1 = inst
        .src_operands
        .get(1)
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    let acc = inst
        .src_operands
        .get(2)
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    format!("{}dp4a.s32.s32 {}, {}, {}, {};", pred, dst, src0, src1, acc)
}

fn ulea_op(inst: &EnhancedSassInstruction, pred: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%ur0".to_string());
    format!("{}mov.u32 {}, 0;", pred, dst)
}

fn lea_op(inst: &EnhancedSassInstruction, pred: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let src = inst
        .src_operands
        .iter()
        .find(|operand| !is_predicate_operand(operand))
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    let shift = inst
        .src_operands
        .iter()
        .rev()
        .find_map(|operand| match operand {
            SassOperand::Immediate(value) => Some(*value),
            _ => None,
        })
        .unwrap_or(0);
    if shift == 0 {
        format!("{}mov.u32 {}, {};", pred, dst, src)
    } else {
        format!("{}shl.b32 {}, {}, {};", pred, dst, src, shift)
    }
}

fn is_predicate_operand(operand: &SassOperand) -> bool {
    matches!(operand, SassOperand::Predicate { .. })
        || matches!(
            operand,
            SassOperand::Register(reg)
                if matches!(reg.prefix.as_str(), "P" | "PT" | "UP" | "UPT")
        )
        || matches!(
            operand,
            SassOperand::Label(label)
                if matches!(
                    label.as_str(),
                    "P0" | "P1" | "P2" | "P3" | "P4" | "P5" | "P6" | "PT"
                        | "UP0" | "UP1" | "UP2" | "UP3" | "UPT"
                )
        )
}

fn load_op(inst: &EnhancedSassInstruction, pred: &str) -> String {
    if is_shared_memory_inst(inst) {
        return shared_load_op(inst, pred);
    }
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let address_operand = inst.src_operands.first();
    let addr = address_operand
        .map(|operand| format_memory_address_operand(inst, operand))
        .unwrap_or_else(|| "[0]".to_string());
    let setup = address_operand
        .and_then(|operand| desc_address_pair_setup(operand, pred))
        .map(|line| format!("{}\n    ", line))
        .unwrap_or_default();
    format!(
        "{}{}ld.{}.{} {}, {};",
        setup,
        pred,
        memory_space_suffix(inst),
        data_type_suffix(inst, SassDataType::U32),
        dst,
        addr
    )
}

fn ldc_op(inst: &EnhancedSassInstruction, pred: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let const_bank = inst.src_operands.first().and_then(constant_bank_operand);
    match const_bank {
        Some((0, offset)) if cuda_launch_constant_value(offset).is_some() => {
            let value = cuda_launch_constant_value(offset).unwrap();
            format!("{}mov.u32 {}, {};", pred, dst, value)
        }
        Some((0, offset)) if cuda_param_index_and_byte_offset(offset).is_some() => {
            let (index, byte_offset) = cuda_param_index_and_byte_offset(offset).unwrap();
            cuda_param_load_op(inst, pred, index, byte_offset)
        }
        Some((0, 0x358)) if is_64bit_modifier(inst) => format!("{}mov.u32 {}, 0;", pred, dst),
        Some((_bank, _offset)) => {
            let ty = data_type_suffix(inst, SassDataType::U32);
            format!(
                "{}mov.{} {}, {};",
                pred,
                ty,
                dst,
                zero_literal_for_type(&ty)
            )
        }
        None => load_op(inst, pred),
    }
}

fn store_op(inst: &EnhancedSassInstruction, pred: &str) -> String {
    if is_shared_memory_inst(inst) {
        return shared_store_op(inst, pred);
    }
    let address_operand = inst.dest_operands.first();
    let addr = address_operand
        .map(|operand| format_memory_address_operand(inst, operand))
        .unwrap_or_else(|| "[0]".to_string());
    let setup = address_operand
        .and_then(|operand| desc_address_pair_setup(operand, pred))
        .map(|line| format!("{}\n    ", line))
        .unwrap_or_default();
    let src = inst
        .src_operands
        .first()
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    format!(
        "{}{}st.{}.{} {}, {};",
        setup,
        pred,
        memory_space_suffix(inst),
        data_type_suffix(inst, SassDataType::U32),
        addr,
        src
    )
}

fn shared_load_op(inst: &EnhancedSassInstruction, pred: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let addr_setup = inst
        .src_operands
        .first()
        .map(shared_address_setup)
        .unwrap_or_else(|| "mov.u64 %rd14, scratch;".to_string());
    format!(
        "{}\n    {}ld.shared.{} {}, [%rd14];",
        addr_setup,
        pred,
        data_type_suffix(inst, SassDataType::U32),
        dst
    )
}

fn shared_store_op(inst: &EnhancedSassInstruction, pred: &str) -> String {
    let addr_setup = inst
        .dest_operands
        .first()
        .map(shared_address_setup)
        .unwrap_or_else(|| "mov.u64 %rd14, scratch;".to_string());
    let src = inst
        .src_operands
        .first()
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());
    format!(
        "{}\n    {}st.shared.{} [%rd14], {};",
        addr_setup,
        pred,
        data_type_suffix(inst, SassDataType::U32),
        src
    )
}

fn shared_address_setup(operand: &SassOperand) -> String {
    match operand {
        SassOperand::Memory { base, offset, .. } => {
            let offset_reg = base
                .as_ref()
                .map(format_register)
                .unwrap_or_else(|| "0".to_string());
            let mut setup = format!(
                "cvt.u64.u32 %rd14, {};\n    mov.u64 %rd13, scratch;\n    add.u64 %rd14, %rd13, %rd14;",
                offset_reg
            );
            if *offset != 0 {
                setup.push_str(&format!("\n    add.u64 %rd14, %rd14, {};", offset));
            }
            setup
        }
        _ => "mov.u64 %rd14, scratch;".to_string(),
    }
}

fn atomic_op(inst: &EnhancedSassInstruction, pred: &str) -> String {
    let first_dest_is_true_predicate = inst
        .dest_operands
        .first()
        .map(is_predicate_true_operand)
        .unwrap_or(false);
    let (dst, addr_idx, value_idx) = if first_dest_is_true_predicate && inst.src_operands.len() >= 3
    {
        (
            inst.src_operands
                .first()
                .map(format_operand)
                .unwrap_or_else(|| "%r0".to_string()),
            1,
            2,
        )
    } else {
        (
            dest_operand(inst).unwrap_or_else(|| "%r0".to_string()),
            0,
            1,
        )
    };
    let addr = inst
        .src_operands
        .get(addr_idx)
        .map(|operand| format_memory_address_operand(inst, operand))
        .unwrap_or_else(|| "[0]".to_string());
    let value = inst
        .src_operands
        .get(value_idx)
        .map(format_operand)
        .unwrap_or_else(|| "0".to_string());

    format!(
        "{}atom.{}.{}.{} {}, {}, {};",
        pred,
        memory_space_suffix(inst),
        atomic_operation_suffix(inst),
        data_type_suffix(inst, SassDataType::U32),
        dst,
        addr,
        value
    )
}

fn atomic_operation_suffix(inst: &EnhancedSassInstruction) -> &'static str {
    if has_modifier(inst, "ADD") {
        "add"
    } else if has_modifier(inst, "MIN") {
        "min"
    } else if has_modifier(inst, "MAX") {
        "max"
    } else if has_modifier(inst, "AND") {
        "and"
    } else if has_modifier(inst, "OR") {
        "or"
    } else if has_modifier(inst, "XOR") {
        "xor"
    } else if has_modifier(inst, "EXCH") {
        "exch"
    } else if has_modifier(inst, "INC") {
        "inc"
    } else if has_modifier(inst, "DEC") {
        "dec"
    } else {
        "add"
    }
}

fn setp_op(
    inst: &EnhancedSassInstruction,
    pred: &str,
    scratch_gpr: Option<&str>,
    scratch_gpr2: Option<&str>,
) -> String {
    if inst.opcode == "FSETP" {
        return fsetp_op(inst, pred, scratch_gpr, scratch_gpr2);
    }

    let dst = dest_operand(inst).unwrap_or_else(|| "%p0".to_string());
    let (src0, src1) = setp_compare_operands(inst);
    let default_ty = match inst.opcode.as_str() {
        "PSETP" => SassDataType::Pred,
        _ => SassDataType::S32,
    };
    format!(
        "{}setp.{}.{} {}, {}, {};",
        pred,
        comparison_suffix(inst),
        setp_data_type_suffix(inst, default_ty, &src0, &src1),
        dst,
        src0,
        src1
    )
}

fn fsetp_op(
    inst: &EnhancedSassInstruction,
    pred: &str,
    scratch_gpr: Option<&str>,
    scratch_gpr2: Option<&str>,
) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%p0".to_string());
    let (src0_operand, src1_operand) = setp_compare_operand_refs(inst);
    let raw_src0 = src0_operand
        .map(format_float_operand)
        .unwrap_or_else(|| format_f32_literal(0.0));
    let raw_src1 = src1_operand
        .map(format_float_operand)
        .unwrap_or_else(|| format_f32_literal(0.0));
    let abs_src0 = src0_operand.and_then(abs_float_operand);
    let abs_src1 = src1_operand.and_then(abs_float_operand);
    // GEU is an unordered floating-point comparison: it is true when either
    // operand is NaN. The generic integer comparison selector does not encode
    // that behavior and otherwise falls back to equality.
    let suffix = if has_modifier(inst, "GEU") {
        "geu".to_string()
    } else {
        comparison_suffix(inst)
    };
    match (abs_src0, abs_src1) {
        (Some(abs0), Some(abs1)) => {
            let scratch0 = scratch_gpr.unwrap_or("%r0");
            let scratch1 = scratch_gpr2.unwrap_or("%r1");
            format!(
                "{}abs.f32 {}, {};\n    {}abs.f32 {}, {};\n    {}setp.{}.f32 {}, {}, {};",
                pred, scratch0, abs0, pred, scratch1, abs1, pred, suffix, dst, scratch0, scratch1
            )
        }
        (Some(abs0), None) => {
            let scratch = scratch_gpr.unwrap_or("%r0");
            format!(
                "{}abs.f32 {}, {};\n    {}setp.{}.f32 {}, {}, {};",
                pred, scratch, abs0, pred, suffix, dst, scratch, raw_src1
            )
        }
        (None, Some(abs1)) => {
            let scratch = scratch_gpr.unwrap_or("%r0");
            format!(
                "{}abs.f32 {}, {};\n    {}setp.{}.f32 {}, {}, {};",
                pred, scratch, abs1, pred, suffix, dst, raw_src0, scratch
            )
        }
        (None, None) => format!(
            "{}setp.{}.f32 {}, {}, {};",
            pred, suffix, dst, raw_src0, raw_src1
        ),
    }
}

fn uisetp_op(inst: &EnhancedSassInstruction, pred: &str) -> String {
    let dst = dest_operand(inst).unwrap_or_else(|| "%up0".to_string());
    let (src0, src1) = uisetp_compare_operands(inst);
    format!(
        "{}setp.{}.{} {}, {}, {};",
        pred,
        comparison_suffix(inst),
        setp_data_type_suffix(inst, SassDataType::U32, &src0, &src1),
        dst,
        src0,
        src1
    )
}

fn setp_data_type_suffix(
    inst: &EnhancedSassInstruction,
    default_ty: SassDataType,
    src0: &str,
    src1: &str,
) -> String {
    let suffix = data_type_suffix(inst, default_ty);
    if matches!(suffix.as_str(), "s64" | "u64")
        && !(src0.starts_with("%rd") && src1.starts_with("%rd"))
    {
        match suffix.as_str() {
            "u64" => "u32".to_string(),
            _ => "s32".to_string(),
        }
    } else {
        suffix
    }
}

fn setp_compare_operands(inst: &EnhancedSassInstruction) -> (String, String) {
    let (src0, src1) = setp_compare_operand_refs(inst);
    (
        src0.map(format_operand).unwrap_or_else(|| "0".to_string()),
        src1.map(format_operand).unwrap_or_else(|| "0".to_string()),
    )
}

fn setp_compare_operand_refs(
    inst: &EnhancedSassInstruction,
) -> (Option<&SassOperand>, Option<&SassOperand>) {
    if inst.src_operands.len() >= 4 && is_pt_register_operand(&inst.src_operands[0]) {
        return (inst.src_operands.get(1), inst.src_operands.get(2));
    }

    (inst.src_operands.first(), inst.src_operands.get(1))
}

fn uisetp_compare_operands(inst: &EnhancedSassInstruction) -> (String, String) {
    if inst.src_operands.len() >= 4 && static_predicate_value(&inst.src_operands[0]).is_some() {
        return (
            format_operand(&inst.src_operands[1]),
            format_operand(&inst.src_operands[2]),
        );
    }

    setp_compare_operands(inst)
}

fn branch_op(inst: &EnhancedSassInstruction, pred: &str) -> String {
    let target = branch_target(inst)
        .map(label_for_address)
        .or_else(|| {
            inst.dest_operands
                .iter()
                .chain(inst.src_operands.iter())
                .next()
                .map(format_operand)
        })
        .unwrap_or_else(|| "L_0000".to_string());
    format!("{}bra {};", pred, target)
}

fn data_type_suffix(inst: &EnhancedSassInstruction, default: SassDataType) -> String {
    match inst.data_type.unwrap_or(default) {
        SassDataType::U8 => "u8",
        SassDataType::U16 => "u16",
        SassDataType::U32 => "u32",
        SassDataType::U64 => "u64",
        SassDataType::U128 => "u128",
        SassDataType::S8 => "s8",
        SassDataType::S16 => "s16",
        SassDataType::S32 => "s32",
        SassDataType::S64 => "s64",
        SassDataType::F16 => "f16",
        SassDataType::F32 => "f32",
        SassDataType::F64 => "f64",
        SassDataType::BF16 => "bf16",
        SassDataType::TF32 => "tf32",
        SassDataType::FP8E4M3 => "e4m3",
        SassDataType::FP8E5M2 => "e5m2",
        SassDataType::B8 => "b8",
        SassDataType::B16 => "b16",
        SassDataType::B32 => "b32",
        SassDataType::B64 => "b64",
        SassDataType::B128 => "b128",
        SassDataType::Pred => "pred",
        SassDataType::Unknown => "b32",
    }
    .to_string()
}

fn memory_space_suffix(inst: &EnhancedSassInstruction) -> String {
    let space = inst.memory_space.or(match inst.opcode.as_str() {
        "LDG" | "ST" | "STG" => Some(SassMemorySpace::Global),
        "LDS" | "STS" => Some(SassMemorySpace::Shared),
        "LDL" | "STL" => Some(SassMemorySpace::Local),
        "LDC" => Some(SassMemorySpace::Constant),
        _ => None,
    });
    match space.unwrap_or(SassMemorySpace::Global) {
        SassMemorySpace::Global => "global",
        SassMemorySpace::Shared => "shared",
        SassMemorySpace::Local => "local",
        SassMemorySpace::Constant => "const",
        SassMemorySpace::Texture => "tex",
        SassMemorySpace::Surface => "surf",
        SassMemorySpace::Generic => "generic",
    }
    .to_string()
}

fn is_shared_memory_inst(inst: &EnhancedSassInstruction) -> bool {
    matches!(inst.memory_space, Some(SassMemorySpace::Shared))
        || matches!(inst.opcode.as_str(), "LDS" | "STS")
}

fn format_memory_address_operand(inst: &EnhancedSassInstruction, operand: &SassOperand) -> String {
    if is_shared_memory_inst(inst) {
        return format_shared_address_operand(operand);
    }
    format_address_operand(operand)
}

fn format_shared_address_operand(operand: &SassOperand) -> String {
    match operand {
        SassOperand::Memory { base, offset, .. } => {
            let mut expr = "scratch".to_string();
            if let Some(base) = base {
                expr.push('+');
                expr.push_str(&format_register(base));
            }
            if *offset > 0 {
                expr.push_str(&format!("+{}", offset));
            } else if *offset < 0 {
                expr.push_str(&offset.to_string());
            }
            format!("[{}]", expr)
        }
        _ => format_address_operand(operand),
    }
}

fn comparison_suffix(inst: &EnhancedSassInstruction) -> String {
    inst.modifiers
        .iter()
        .find_map(|modifier| {
            let normalized = modifier.trim_start_matches('.').to_ascii_uppercase();
            match normalized.as_str() {
                "EQ" => Some("eq"),
                "NE" | "NEU" => Some("ne"),
                "LT" | "LO" => Some("lt"),
                "LE" | "LS" => Some("le"),
                "GT" | "HI" => Some("gt"),
                "GE" | "HS" => Some("ge"),
                _ => None,
            }
        })
        .unwrap_or("eq")
        .to_string()
}

fn format_address_operand(operand: &SassOperand) -> String {
    match operand {
        SassOperand::ConstantBank { bank, offset } => {
            format!("[c[0x{:x}][0x{:x}]]", bank, offset)
        }
        SassOperand::Memory {
            base,
            offset,
            index,
            scale,
        } => {
            let mut expr = base
                .as_ref()
                .map(format_register)
                .unwrap_or_else(|| "0".to_string());
            if let Some(index) = index {
                if expr != "0" {
                    expr.push('+');
                } else {
                    expr.clear();
                }
                expr.push_str(&format_register(index));
                if *scale != 1 {
                    expr.push_str(&format!("*{}", scale));
                }
            }
            if *offset > 0 {
                if expr.is_empty() {
                    expr.push_str(&offset.to_string());
                } else {
                    expr.push_str(&format!("+{}", offset));
                }
            } else if *offset < 0 {
                expr.push_str(&offset.to_string());
            }
            if expr.is_empty() {
                expr.push('0');
            }
            format!("[{}]", expr)
        }
        SassOperand::Address(address) => format!("[{}]", label_for_address(*address)),
        SassOperand::Label(label) => {
            desc_address_to_ptx(label).unwrap_or_else(|| format!("[{}]", label))
        }
        _ => format!("[{}]", format_operand(operand)),
    }
}

fn dest_operand(inst: &EnhancedSassInstruction) -> Option<String> {
    inst.dest_operands.first().map(format_operand)
}

fn dest_rd_operand(inst: &EnhancedSassInstruction) -> Option<String> {
    inst.dest_operands.first().map(format_rd_operand)
}

fn predicate_prefix(inst: &EnhancedSassInstruction) -> String {
    match &inst.predicate {
        Some(SassOperand::Predicate { register, negated }) => {
            if *negated {
                format!("@!{} ", format_register(register))
            } else {
                format!("@{} ", format_register(register))
            }
        }
        _ => String::new(),
    }
}

fn inverted_predicate_prefix(inst: &EnhancedSassInstruction) -> Option<String> {
    match &inst.predicate {
        Some(SassOperand::Predicate { register, negated }) => {
            let reg = format_register(register);
            if *negated {
                Some(format!("@{} ", reg))
            } else {
                Some(format!("@!{} ", reg))
            }
        }
        _ => None,
    }
}

fn format_rd_operand(operand: &SassOperand) -> String {
    match operand {
        SassOperand::Register(reg) if reg.prefix == "R" && !reg.is_zero => {
            format!("%rd{}", reg.number)
        }
        SassOperand::Label(label) => desc_address_to_ptx(label).unwrap_or_else(|| label.clone()),
        _ => format_operand(operand),
    }
}

fn constant_bank_operand(operand: &SassOperand) -> Option<(u32, u32)> {
    match operand {
        SassOperand::ConstantBank { bank, offset } => Some((*bank, *offset)),
        _ => None,
    }
}

fn cuda_launch_constant_value(offset: u32) -> Option<&'static str> {
    match offset {
        0x360 => Some("%ntid.x"),
        0x364 => Some("%ntid.y"),
        0x368 => Some("%ntid.z"),
        0x370 => Some("%nctaid.x"),
        0x374 => Some("%nctaid.y"),
        0x378 => Some("%nctaid.z"),
        // ptxas emits this metadata read in SM120 kernels even when the
        // destination is dead. PTX has no corresponding special register.
        0x37c => Some("0"),
        _ => None,
    }
}

fn cuda_param_index_and_byte_offset(offset: u32) -> Option<(u32, u32)> {
    let relative = offset.checked_sub(0x380)?;
    Some((relative / 8, relative % 8))
}

fn cuda_param_load_op(
    inst: &EnhancedSassInstruction,
    pred: &str,
    index: u32,
    byte_offset: u32,
) -> String {
    let name = cuda_param_name(
        index,
        if is_64bit_modifier(inst) || byte_offset >= 4 {
            CudaParamWidth::U64
        } else {
            CudaParamWidth::U32
        },
    );
    if is_64bit_modifier(inst) && byte_offset == 0 {
        if let Some((low, high)) = uniform_register_pair_dest(inst) {
            return format!(
                "{}ld.param.u32 {}, [{}];\n    {}ld.param.u32 {}, [{}+4];",
                pred, low, name, pred, high, name
            );
        }
        let wide = dest_rd_operand(inst).unwrap_or_else(|| "%rd0".to_string());
        let load = format!("{}ld.param.u64 {}, [{}];", pred, wide, name);
        return match inst.dest_operands.first().and_then(|operand| pair_write(operand, pred)) {
            Some(unpack) => format!("{}\n    {}", load, unpack),
            None => load,
        };
    }

    let dst = dest_operand(inst).unwrap_or_else(|| "%r0".to_string());
    let address = if byte_offset == 0 {
        name
    } else {
        format!("{}+{}", name, byte_offset)
    };
    format!("{}ld.param.u32 {}, [{}];", pred, dst, address)
}

fn uniform_register_pair_dest(inst: &EnhancedSassInstruction) -> Option<(String, String)> {
    let Some(SassOperand::Register(reg)) = inst.dest_operands.first() else {
        return None;
    };
    if reg.prefix != "UR" || reg.is_zero {
        return None;
    }
    let low = format_register(reg);
    let mut high_reg = reg.clone();
    high_reg.number += 1;
    Some((low, format_register(&high_reg)))
}

fn has_modifier(inst: &EnhancedSassInstruction, modifier: &str) -> bool {
    inst.modifiers
        .iter()
        .any(|m| m.eq_ignore_ascii_case(modifier))
}

fn is_64bit_modifier(inst: &EnhancedSassInstruction) -> bool {
    has_modifier(inst, "64")
        || matches!(
            inst.data_type,
            Some(SassDataType::U64 | SassDataType::S64 | SassDataType::B64)
        )
}

fn is_wide_memory_data(inst: &EnhancedSassInstruction) -> bool {
    matches!(inst.data_type, Some(SassDataType::U64 | SassDataType::S64
        | SassDataType::B64 | SassDataType::U128))
}

fn is_extended_iadd3(inst: &EnhancedSassInstruction) -> bool {
    inst.opcode == "IADD3" && inst.src_operands.len() > 3
}

fn is_lop3_xor(inst: &EnhancedSassInstruction) -> bool {
    is_lop3_binary_truth_table(inst, 0x3c)
}

fn is_lop3_and(inst: &EnhancedSassInstruction) -> bool {
    is_lop3_binary_truth_table(inst, 0xc0)
}

fn is_lop3_or(inst: &EnhancedSassInstruction) -> bool {
    is_lop3_binary_truth_table(inst, 0xfc)
}

fn is_lop3_lut(inst: &EnhancedSassInstruction) -> bool {
    inst.opcode == "LOP3"
        && inst.src_operands.len() >= 4
        && matches!(inst.src_operands.get(3), Some(SassOperand::Immediate(_)))
        && !matches!(
            inst.dest_operands.first(),
            Some(SassOperand::Register(reg))
                if reg.prefix == "P" || reg.prefix == "UP" || reg.prefix == "PT"
        )
        && !matches!(
            inst.dest_operands.first(),
            Some(SassOperand::Predicate { .. })
        )
}

fn is_lop3_predicate_lut(inst: &EnhancedSassInstruction) -> bool {
    inst.opcode == "LOP3"
        && inst.src_operands.len() >= 5
        && matches!(inst.src_operands.get(4), Some(SassOperand::Immediate(_)))
        && matches!(
            inst.dest_operands.first(),
            Some(SassOperand::Register(reg)) if reg.prefix == "P" || reg.prefix == "UP"
        )
}

fn is_ulop3_lut(inst: &EnhancedSassInstruction) -> bool {
    inst.opcode == "ULOP3"
        && inst.src_operands.len() >= 4
        && matches!(inst.src_operands.get(3), Some(SassOperand::Immediate(_)))
}

fn is_idp_4a_s8_s8(inst: &EnhancedSassInstruction) -> bool {
    inst.opcode == "IDP"
        && has_modifier(inst, "4A")
        && inst.modifiers.iter().filter(|m| m.as_str() == "S8").count() >= 2
}

fn is_plop3_binary_lut(inst: &EnhancedSassInstruction) -> bool {
    plop3_binary_lut_operator(inst).is_some()
}

fn plop3_binary_lut_operator(inst: &EnhancedSassInstruction) -> Option<&'static str> {
    if inst.opcode != "PLOP3" || inst.src_operands.len() < 5 {
        return None;
    }
    if !is_predicate_true_operand(inst.src_operands.first()?) {
        return None;
    }
    let lut = match inst.src_operands.get(4) {
        Some(SassOperand::Immediate(value)) => *value,
        _ => return None,
    };
    let mut reduced_lut = 0i64;
    for lhs in 0..=1 {
        for rhs in 0..=1 {
            let original_index = 1 | (lhs << 1) | (rhs << 2);
            if ((lut >> original_index) & 1) != 0 {
                let reduced_index = lhs | (rhs << 1);
                reduced_lut |= 1 << reduced_index;
            }
        }
    }
    match reduced_lut {
        0x8 => Some("and"),
        0xe => Some("or"),
        0x6 => Some("xor"),
        _ => None,
    }
}

fn is_lop_lut(inst: &EnhancedSassInstruction) -> bool {
    inst.opcode == "LOP"
        && inst.src_operands.len() >= 4
        && matches!(inst.src_operands.get(3), Some(SassOperand::Immediate(_)))
        && !matches!(
            inst.dest_operands.first(),
            Some(SassOperand::Register(reg))
                if reg.prefix == "P" || reg.prefix == "UP" || reg.prefix == "PT"
        )
        && !matches!(
            inst.dest_operands.first(),
            Some(SassOperand::Predicate { .. })
        )
}

fn lop_binary_operator(inst: &EnhancedSassInstruction) -> Option<&'static str> {
    if inst.opcode != "LOP" {
        return None;
    }
    if inst
        .src_operands
        .iter()
        .filter_map(format_integer_data_operand)
        .take(2)
        .count()
        < 2
    {
        return None;
    }
    if has_modifier(inst, "OR") {
        Some("or")
    } else if has_modifier(inst, "XOR") {
        Some("xor")
    } else {
        Some("and")
    }
}

fn is_lop3_binary_truth_table(inst: &EnhancedSassInstruction, lut: i64) -> bool {
    inst.opcode == "LOP3"
        && inst.src_operands.len() >= 4
        && matches!(inst.src_operands.get(3), Some(SassOperand::Immediate(value)) if *value == lut)
        && inst
            .src_operands
            .get(2)
            .map(is_zero_register_operand)
            .unwrap_or(false)
}

fn is_lop3_odd_predicate(inst: &EnhancedSassInstruction) -> bool {
    if inst.opcode != "LOP3" || inst.src_operands.len() < 5 {
        return false;
    }
    let dest_is_pred = matches!(
        inst.dest_operands.first(),
        Some(SassOperand::Register(reg)) if reg.prefix == "P"
    );
    dest_is_pred
        && inst
            .src_operands
            .first()
            .map(is_zero_register_operand)
            .unwrap_or(false)
        && matches!(inst.src_operands.get(2), Some(SassOperand::Immediate(1)))
        && matches!(inst.src_operands.get(4), Some(SassOperand::Immediate(0xc0)))
}

fn is_shf_left_rotate(inst: &EnhancedSassInstruction) -> bool {
    inst.opcode == "SHF"
        && has_modifier(inst, "HI")
        && !has_modifier(inst, "R")
        && shift_amount_immediate(inst)
            .map(|amount| (1..32).contains(&amount))
            .unwrap_or(false)
        && operands_same_register(inst.src_operands.first(), inst.src_operands.get(2))
}

fn is_hadd2_zero_source(inst: &EnhancedSassInstruction) -> bool {
    inst.opcode == "HADD2" && inst.src_operands.iter().any(is_half2_zero_operand)
}

fn has_abs_float_operand(inst: &EnhancedSassInstruction) -> bool {
    match inst.opcode.as_str() {
        "FADD" | "FMUL" | "FMNMX" => inst
            .src_operands
            .iter()
            .take(2)
            .any(|operand| abs_float_operand(operand).is_some()),
        "FFMA" => inst
            .src_operands
            .iter()
            .take(3)
            .any(|operand| abs_float_operand(operand).is_some()),
        "FSETP" => abs_setp_compare_operand_count(inst) > 0,
        _ => false,
    }
}

fn fmnmx_operator(inst: &EnhancedSassInstruction) -> &'static str {
    match inst.src_operands.get(2).and_then(static_predicate_value) {
        Some(false) => "max",
        _ => "min",
    }
}

fn abs_float_operand(operand: &SassOperand) -> Option<String> {
    let SassOperand::Label(label) = operand else {
        return None;
    };
    let inner = absolute_label_inner(label)?;
    parse_annotated_sass_operand(inner).map(|operand| format_float_operand(&operand))
}

fn abs_setp_compare_operand_count(inst: &EnhancedSassInstruction) -> usize {
    let (src0, src1) = setp_compare_operand_refs(inst);
    [src0, src1]
        .into_iter()
        .flatten()
        .filter(|operand| abs_float_operand(operand).is_some())
        .count()
}

fn absolute_label_inner(label: &str) -> Option<&str> {
    strip_sass_operand_suffixes(label)
        .strip_prefix('|')?
        .strip_suffix('|')
}

fn parse_annotated_sass_operand(label: &str) -> Option<SassOperand> {
    let normalized = strip_sass_operand_suffixes(label);
    let operand = SassOperand::parse(normalized)?;
    match &operand {
        SassOperand::Label(parsed) if parsed == normalized => None,
        _ => Some(operand),
    }
}

fn strip_sass_operand_suffixes(mut label: &str) -> &str {
    while let Some(stripped) = label.strip_suffix(".reuse") {
        label = stripped;
    }
    label
}

fn vimnmx_static_operator(inst: &EnhancedSassInstruction) -> Option<&'static str> {
    match static_predicate_value(inst.src_operands.get(2)?)? {
        true => Some("min"),
        false => Some("max"),
    }
}

fn static_predicate_value(operand: &SassOperand) -> Option<bool> {
    match operand {
        SassOperand::Register(reg) if reg.prefix == "PT" => Some(true),
        SassOperand::Predicate { register, negated } if register.prefix == "PT" => Some(!negated),
        SassOperand::Label(label) => match label.as_str() {
            "PT" | "UPT" => Some(true),
            "!PT" | "!UPT" => Some(false),
            _ => None,
        },
        _ => None,
    }
}

fn shift_amount_immediate(inst: &EnhancedSassInstruction) -> Option<i64> {
    match inst.src_operands.get(1) {
        Some(SassOperand::Immediate(amount)) => Some(*amount),
        _ => None,
    }
}

fn operands_same_register(lhs: Option<&SassOperand>, rhs: Option<&SassOperand>) -> bool {
    matches!((lhs, rhs), (Some(SassOperand::Register(a)), Some(SassOperand::Register(b))) if a == b)
}

fn is_zero_register_operand(operand: &SassOperand) -> bool {
    matches!(operand, SassOperand::Register(reg) if reg.is_zero)
}

fn is_pt_register_operand(operand: &SassOperand) -> bool {
    matches!(operand, SassOperand::Register(reg) if reg.prefix == "PT")
}

fn is_predicate_true_operand(operand: &SassOperand) -> bool {
    matches!(operand, SassOperand::Register(reg) if reg.prefix == "PT")
}

fn desc_address_register_number(label: &str) -> Option<u32> {
    let marker = "][R";
    let reg_start = label.find(marker)? + marker.len();
    let rest = &label[reg_start..];
    let digits: String = rest.chars().take_while(|ch| ch.is_ascii_digit()).collect();
    if digits.is_empty() {
        return None;
    }
    digits.parse::<u32>().ok()
}

fn desc_address_pair_setup(operand: &SassOperand, pred: &str) -> Option<String> {
    let SassOperand::Label(label) = operand else { return None; };
    if !label.contains(".64") { return None; }
    let reg_num = desc_address_register_number(label)?;
    Some(format!("{}mov.b64 %rd{}, {{%r{}, %r{}}};",
                 pred, reg_num, reg_num, reg_num + 1))
}

fn desc_address_to_ptx(label: &str) -> Option<String> {
    let reg_num = desc_address_register_number(label)?;
    let marker = "][R";
    let reg_start = label.find(marker)? + marker.len();
    let rest = &label[reg_start..];
    let digits: String = rest.chars().take_while(|ch| ch.is_ascii_digit()).collect();
    let tail = rest[digits.len()..].split(']').next().unwrap_or_default();
    let offset = tail
        .char_indices()
        .find(|(_, ch)| matches!(ch, '+' | '-'))
        .and_then(|(idx, _)| parse_signed_hex_or_dec(&tail[idx..]))
        .unwrap_or(0);
    if offset > 0 {
        Some(format!("[%rd{}+{}]", reg_num, offset))
    } else if offset < 0 {
        Some(format!("[%rd{}{}]", reg_num, offset))
    } else {
        Some(format!("[%rd{}]", reg_num))
    }
}

fn parse_signed_hex_or_dec(value: &str) -> Option<i64> {
    let value = value.trim();
    let (sign, digits) = match value.as_bytes().first().copied() {
        Some(b'+') => (1i64, &value[1..]),
        Some(b'-') => (-1i64, &value[1..]),
        _ => (1i64, value),
    };
    let magnitude = digits
        .strip_prefix("0x")
        .or_else(|| digits.strip_prefix("0X"))
        .map(|hex| i64::from_str_radix(hex, 16))
        .unwrap_or_else(|| digits.parse::<i64>())
        .ok()?;
    Some(sign * magnitude)
}

fn uses_shared_memory(instructions: &[EnhancedSassInstruction]) -> bool {
    instructions.iter().any(|inst| {
        matches!(inst.memory_space, Some(SassMemorySpace::Shared))
            || matches!(inst.opcode.as_str(), "LDS" | "STS")
    })
}

fn format_operand(operand: &SassOperand) -> String {
    match operand {
        SassOperand::Register(reg) => format_register(reg),
        SassOperand::Predicate { register, negated }
            if matches!(register.prefix.as_str(), "PT" | "UPT") =>
        {
            static_bool_literal(!*negated).to_string()
        }
        SassOperand::Predicate { register, negated } => {
            if *negated {
                format!("!{}", format_register(register))
            } else {
                format_register(register)
            }
        }
        SassOperand::Immediate(value) => value.to_string(),
        SassOperand::FloatImmediate(value) => format_f32_literal(*value),
        SassOperand::ConstantBank { bank, offset } => format!("c[0x{:x}][0x{:x}]", bank, offset),
        SassOperand::Memory { base, offset, .. } => {
            let base = base
                .as_ref()
                .map(format_register)
                .unwrap_or_else(|| "0".to_string());
            if *offset == 0 {
                format!("[{}]", base)
            } else if *offset > 0 {
                format!("[{}+{}]", base, offset)
            } else {
                format!("[{}{}]", base, offset)
            }
        }
        SassOperand::Barrier(id) => id.to_string(),
        SassOperand::SpecialRegister(name) => map_special_register(name),
        SassOperand::Label(label) if matches!(label.as_str(), "SRZ" | "SR_Z") => "0".to_string(),
        SassOperand::Label(label) if matches!(label.as_str(), "PT" | "UPT" | "!PT" | "!UPT") => {
            static_bool_literal(matches!(label.as_str(), "PT" | "UPT")).to_string()
        }
        SassOperand::Label(label) => format_label_operand(label),
        SassOperand::Address(address) => label_for_address(*address),
    }
}

fn format_label_operand(label: &str) -> String {
    if let Some(inner) = absolute_label_inner(label) {
        if let Some(operand) = parse_annotated_sass_operand(inner) {
            return format_operand(&operand);
        }
    }
    if let Some(operand) = parse_annotated_sass_operand(label) {
        return format_operand(&operand);
    }
    label.to_string()
}

fn format_signed_operand(operand: &SassOperand) -> (String, bool) {
    match operand {
        SassOperand::Register(reg) if reg.prefix.starts_with('-') => {
            (format_register_without_negation(reg), true)
        }
        SassOperand::Immediate(value) if *value < 0 => (value.abs().to_string(), true),
        _ => (format_operand(operand), false),
    }
}

fn format_float_operand(operand: &SassOperand) -> String {
    if is_zero_register_operand(operand)
        || matches!(operand, SassOperand::Label(label) if matches!(label.as_str(), "RZ" | "-RZ" | "URZ" | "-URZ"))
    {
        return format_f32_literal(0.0);
    }
    match operand {
        SassOperand::Immediate(value) => format_f32_literal(*value as f64),
        SassOperand::FloatImmediate(value) => format_f32_literal(*value),
        _ => format_operand(operand),
    }
}

fn format_f32_pack_operand(operand: &SassOperand) -> String {
    if is_half2_zero_operand(operand) {
        "0f00000000".to_string()
    } else {
        format_operand(operand)
    }
}

fn format_half2_operand(operand: &SassOperand, zero_scratch: &str) -> String {
    if is_half2_zero_operand(operand) {
        zero_scratch.to_string()
    } else {
        format_operand(operand)
    }
}

fn is_half2_zero_operand(operand: &SassOperand) -> bool {
    match operand {
        SassOperand::Register(reg) => reg.is_zero,
        SassOperand::Immediate(value) => *value == 0,
        SassOperand::Label(label) => matches!(label.as_str(), "RZ" | "-RZ" | "URZ" | "-URZ"),
        _ => false,
    }
}

fn format_hex_immediate_operand(operand: &SassOperand) -> String {
    match operand {
        SassOperand::Immediate(value) if *value >= 0 => format!("0x{:x}", value),
        _ => format_operand(operand),
    }
}

fn format_predicate_logic_operand(operand: &SassOperand) -> String {
    match operand {
        SassOperand::Register(reg) if reg.prefix == "PT" => "1".to_string(),
        SassOperand::Register(reg) if reg.prefix == "P" => format!("%p{}", reg.number),
        SassOperand::Register(reg) if reg.prefix == "UP" => format!("%up{}", reg.number),
        SassOperand::Predicate { register, negated }
            if matches!(register.prefix.as_str(), "PT" | "UPT") =>
        {
            static_bool_literal(!*negated).to_string()
        }
        SassOperand::Predicate { register, negated } if *negated => {
            format!("!{}", format_register(register))
        }
        SassOperand::Predicate { register, .. } => format_register(register),
        _ => format_operand(operand),
    }
}

fn format_integer_data_operand(operand: &SassOperand) -> Option<String> {
    match operand {
        SassOperand::Register(reg)
            if matches!(reg.prefix.as_str(), "R" | "UR" | "RZ" | "URZ") || reg.is_zero =>
        {
            Some(format_register(reg))
        }
        SassOperand::Immediate(_) => Some(format_operand(operand)),
        _ => None,
    }
}

fn extract_sass_encoding(inst: &EnhancedSassInstruction) -> Option<u64> {
    let marker = "/* 0x";
    let start = inst.instruction_text.rfind(marker)? + marker.len();
    let end = inst.instruction_text[start..].find(" */")? + start;
    u64::from_str_radix(&inst.instruction_text[start..end], 16).ok()
}

fn format_register_without_negation(reg: &SassRegister) -> String {
    let prefix = reg.prefix.trim_start_matches('-');
    if reg.is_zero {
        return "%rz".to_string();
    }
    match prefix {
        "R" => format!("%r{}", reg.number),
        "UR" => format!("%ur{}", reg.number),
        "P" => format!("%p{}", reg.number),
        "UP" => format!("%up{}", reg.number),
        _ => format_register(reg),
    }
}

fn static_bool_literal(value: bool) -> &'static str {
    if value {
        "1"
    } else {
        "0"
    }
}

fn format_f32_literal(value: f64) -> String {
    format!("0f{:08x}", (value as f32).to_bits())
}

fn zero_literal_for_type(ty: &str) -> String {
    match ty {
        "f32" => format_f32_literal(0.0),
        "f64" => "0d0000000000000000".to_string(),
        _ => "0".to_string(),
    }
}

fn format_sel_predicate_operand(operand: &SassOperand) -> (String, bool) {
    match operand {
        SassOperand::Predicate { register, negated } => (format_register(register), *negated),
        SassOperand::Register(reg) if reg.prefix == "P" => (format_register(reg), false),
        SassOperand::Register(reg) if reg.prefix == "!P" => (format!("%p{}", reg.number), true),
        SassOperand::Register(reg) if reg.prefix == "UP" => (format_register(reg), false),
        SassOperand::Register(reg) if reg.prefix == "!UP" => (format!("%up{}", reg.number), true),
        SassOperand::Label(label) if label == "PT" => ("1".to_string(), false),
        SassOperand::Label(label) if label == "!PT" => ("1".to_string(), true),
        SassOperand::Label(label) if label == "UPT" => ("1".to_string(), false),
        SassOperand::Label(label) if label == "!UPT" => ("1".to_string(), true),
        SassOperand::Label(label) if is_predicate_label(label) => {
            (format!("%p{}", &label[1..]), false)
        }
        SassOperand::Label(label) if label.starts_with("!P") && is_predicate_label(&label[1..]) => {
            (format!("%p{}", &label[2..]), true)
        }
        SassOperand::Label(label) if is_uniform_predicate_label(label) => {
            (format!("%up{}", &label[2..]), false)
        }
        SassOperand::Label(label)
            if label.starts_with("!UP") && is_uniform_predicate_label(&label[1..]) =>
        {
            (format!("%up{}", &label[3..]), true)
        }
        _ => (format_operand(operand), false),
    }
}

fn is_predicate_label(label: &str) -> bool {
    label
        .strip_prefix('P')
        .is_some_and(|number| !number.is_empty() && number.chars().all(|ch| ch.is_ascii_digit()))
}

fn is_uniform_predicate_label(label: &str) -> bool {
    label
        .strip_prefix("UP")
        .is_some_and(|number| !number.is_empty() && number.chars().all(|ch| ch.is_ascii_digit()))
}

fn format_register(reg: &SassRegister) -> String {
    if reg.is_zero {
        return "0".to_string();
    }
    match reg.prefix.as_str() {
        "R" => format!("%r{}", reg.number),
        "P" => format!("%p{}", reg.number),
        "UR" => format!("%ur{}", reg.number),
        "UP" => format!("%up{}", reg.number),
        _ => format!("%r{}", reg.number),
    }
}

fn map_special_register(name: &str) -> String {
    match name {
        "SRZ" | "SR_Z" => "0".to_string(),
        "SR_TID.X" | "SR_TIDX" => "%tid.x".to_string(),
        "SR_TID.Y" | "SR_TIDY" => "%tid.y".to_string(),
        "SR_TID.Z" | "SR_TIDZ" => "%tid.z".to_string(),
        "SR_CTAID.X" | "SR_CTAIDX" => "%ctaid.x".to_string(),
        "SR_CTAID.Y" | "SR_CTAIDY" => "%ctaid.y".to_string(),
        "SR_CTAID.Z" | "SR_CTAIDZ" => "%ctaid.z".to_string(),
        "SR_NTID.X" | "SR_NTIDX" => "%ntid.x".to_string(),
        "SR_NTID.Y" | "SR_NTIDY" => "%ntid.y".to_string(),
        "SR_NTID.Z" | "SR_NTIDZ" => "%ntid.z".to_string(),
        "SR_NCTAID.X" | "SR_NCTAIDX" => "%nctaid.x".to_string(),
        "SR_NCTAID.Y" | "SR_NCTAIDY" => "%nctaid.y".to_string(),
        "SR_NCTAID.Z" | "SR_NCTAIDZ" => "%nctaid.z".to_string(),
        "SR_CgaCtaId" | "SR_CGACTAID" => "0".to_string(),
        "SR_LANEID" => "%laneid".to_string(),
        "SR_WARPID" => "%warpid".to_string(),
        "SR_CLOCK" | "SR_CLOCK_LO" => "%clock".to_string(),
        "SR_CLOCK_HI" => "%clock_hi".to_string(),
        _ => format!("%{}", name.trim_start_matches("SR_").to_ascii_lowercase()),
    }
}

fn branch_target(inst: &EnhancedSassInstruction) -> Option<u64> {
    inst.dest_operands
        .iter()
        .chain(inst.src_operands.iter())
        .find_map(|operand| match operand {
            SassOperand::Immediate(value) if *value >= 0 => Some(*value as u64),
            SassOperand::Address(address) => Some(*address),
            _ => None,
        })
}

fn sanitize_ident(name: &str) -> String {
    let mut out = String::new();
    for (i, ch) in name.chars().enumerate() {
        if ch == '_' || ch.is_ascii_alphanumeric() && (i > 0 || !ch.is_ascii_digit()) {
            out.push(ch);
        } else {
            out.push('_');
        }
    }
    if out.is_empty() {
        "kernel".to_string()
    } else {
        out
    }
}

fn label_for_address(address: u64) -> String {
    format!("L_{:04x}", address)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sass::{
        CubinKernel, EnhancedSassInstruction, ParsedCubin, SassDataType, SassMemorySpace,
        SassOpcodeClass, SassOperand, SassRegister,
    };
    use std::collections::HashMap;

    fn reg(n: u32) -> SassOperand {
        SassOperand::Register(SassRegister::new("R", n))
    }

    fn pred(n: u32) -> SassOperand {
        SassOperand::Predicate {
            register: SassRegister::new("P", n),
            negated: false,
        }
    }

    fn mem(base: u32, offset: i64) -> SassOperand {
        SassOperand::Memory {
            base: Some(SassRegister::new("R", base)),
            offset,
            index: None,
            scale: 1,
        }
    }

    #[test]
    fn sass_lifter_fsel_negative_qnan_preserves_encoded_payload() {
        let text = r#"Function : fsel_qnan
        /*0050*/ FSEL R5, RZ, -QNAN , P0 ; /* 0xffffffffff057808 */
                 /* 0x000fca0000000000 */
        /*0060*/ EXIT ; /* 0x000000000000794d */
                 /* 0x000fea0003800000 */"#;
        let options = SassLiftOptions {
            sm_version: 120,
            kernel_name: "fsel_qnan".to_string(),
            include_sass_comments: false,
            emit_unsupported_comments: true,
        };
        let all_ones = lift_sass_text_to_ptx(text, options.clone()).unwrap();
        assert!(
            all_ones.diagnostics.is_empty(),
            "{:?}",
            all_ones.diagnostics
        );
        assert!(all_ones.ptx.contains("selp.b32 %r5, 0, 0xffffffff, %p0;"));

        let canonical = lift_sass_text_to_ptx(
            &text.replace("0xffffffffff057808", "0xffc00000ff057808"),
            options.clone(),
        )
        .unwrap();
        assert!(
            canonical.diagnostics.is_empty(),
            "{:?}",
            canonical.diagnostics
        );
        assert!(canonical.ptx.contains("selp.b32 %r5, 0, 0xffc00000, %p0;"));

        let missing =
            lift_sass_text_to_ptx(&text.replace("/* 0xffffffffff057808 */", ""), options).unwrap();
        assert_eq!(missing.diagnostics.len(), 1);
        assert!(!missing.ptx.contains("selp.b32 %r5, 0, -QNAN"));
    }

    #[test]
    fn sass_lifter_text_frontend_uses_function_name_and_sm120() {
        let text = r#"Function : vector_add
        /*0000*/                   S2R R0, SR_TID.X ;
        /*0010*/                   LDG.E.U32 R1, [R2] ;
        /*0020*/                   STG.E.U32 [R3], R1 ;
        /*0030*/                   EXIT ;
"#;

        let result = lift_sass_text_to_ptx(
            text,
            SassLiftOptions {
                sm_version: 120,
                kernel_name: String::new(),
                include_sass_comments: true,
                emit_unsupported_comments: true,
            },
        )
        .expect("text frontend should lift cuobjdump text");

        assert!(result.ptx.contains(".visible .entry vector_add()"));
        assert!(result.ptx.contains(".target sm_120"));
        assert!(result.ptx.contains("ld.global.u32 %r1, [%r2];"));
        assert!(result.ptx.contains("st.global.u32 [%r3], %r1;"));
    }

    #[test]
    fn sass_lifter_text_frontend_default_options_infer_single_function_name() {
        let text = r#"Function : vector_add
        /*0000*/                   S2R R0, SR_TID.X ;
        /*0010*/                   EXIT ;
"#;

        let result = lift_sass_text_to_ptx(text, SassLiftOptions::default())
            .expect("default text frontend should infer an unambiguous function name");

        assert!(result.ptx.contains(".visible .entry vector_add()"));
    }

    #[test]
    fn sass_lifter_text_frontend_rejects_empty_input() {
        let err = lift_sass_text_to_ptx("", SassLiftOptions::default())
            .expect_err("empty text should not lift");

        assert!(err.contains("No SASS instructions parsed"), "{err}");
    }

    #[test]
    fn sass_lifter_text_frontend_lifts_unspecified_multi_function_input() {
        let text = r#"Function : first
        /*0000*/                   S2R R0, SR_TID.X ;
        /*0010*/                   EXIT ;
Function : second
        /*0020*/                   IADD R4, R5, 7 ;
        /*0030*/                   EXIT ;
"#;

        let result = lift_sass_text_to_ptx(text, SassLiftOptions::default())
            .expect("default multi-function text should lift all functions");

        assert!(result.ptx.contains(".visible .entry first()"));
        assert!(result.ptx.contains(".visible .entry second()"));
        assert!(result.ptx.contains("mov.u32 %r0, %tid.x;"));
        assert!(result.ptx.contains("add.s32 %r4, %r5, 7;"));

        let result = lift_sass_text_to_ptx(
            text,
            SassLiftOptions {
                kernel_name: String::new(),
                ..SassLiftOptions::default()
            },
        )
        .expect("empty kernel_name should lift all functions");

        assert!(result.ptx.contains(".visible .entry first()"));
        assert!(result.ptx.contains(".visible .entry second()"));
    }

    #[test]
    fn sass_lifter_text_frontend_selects_requested_function_only() {
        let text = r#"Function : first
        /*0000*/                   S2R R0, SR_TID.X ;
        /*0010*/                   EXIT ;
Function : second
        /*0020*/                   IADD R4, R5, 7 ;
        /*0030*/                   EXIT ;
"#;

        let result = lift_sass_text_to_ptx(
            text,
            SassLiftOptions {
                kernel_name: "second".to_string(),
                include_sass_comments: false,
                ..SassLiftOptions::default()
            },
        )
        .expect("explicit selector should lift only the requested function");

        assert!(result.ptx.contains(".visible .entry second()"));
        assert!(result.ptx.contains("add.s32 %r4, %r5, 7;"));
        assert!(result.ptx.contains("L_0020:"));
        assert!(!result.ptx.contains("mov.u32 %r0, %tid.x;"));
        assert!(!result.ptx.contains("L_0000:"));
    }

    #[test]
    fn sass_lifter_text_frontend_selects_literal_kernel_function_name() {
        let text = r#"Function : first
        /*0000*/                   S2R R0, SR_TID.X ;
        /*0010*/                   EXIT ;
Function : kernel
        /*0020*/                   IADD R4, R5, 7 ;
        /*0030*/                   EXIT ;
"#;

        let result = lift_sass_text_to_ptx(
            text,
            SassLiftOptions {
                kernel_name: "kernel".to_string(),
                include_sass_comments: false,
                ..SassLiftOptions::default()
            },
        )
        .expect("literal kernel selector should select Function : kernel");

        assert!(result.ptx.contains(".visible .entry kernel()"));
        assert!(result.ptx.contains("add.s32 %r4, %r5, 7;"));
        assert!(result.ptx.contains("L_0020:"));
        assert!(!result.ptx.contains("mov.u32 %r0, %tid.x;"));
        assert!(!result.ptx.contains("L_0000:"));
    }

    #[test]
    fn sass_lifter_cubin_selection_uses_requested_kernel_name() {
        let parsed = ParsedCubin {
            sm_version: 120,
            ptx_version: None,
            kernels: vec![
                test_cubin_kernel("first", 0x100),
                test_cubin_kernel("kernel", 0x180),
                test_cubin_kernel("second", 0x200),
            ],
            constants: Vec::new(),
            debug_lines: HashMap::new(),
            symbols: Vec::new(),
            sections: HashMap::new(),
        };

        let selected =
            select_cubin_kernel(&parsed, "second").expect("requested kernel should be selected");
        assert_eq!(selected.name, "second");
        assert_eq!(selected.address, 0x200);

        let selected =
            select_cubin_kernel(&parsed, "").expect("empty selector should use the first kernel");
        assert_eq!(selected.name, "first");
        assert_eq!(selected.address, 0x100);

        let selected = select_cubin_kernel(&parsed, "kernel")
            .expect("literal kernel selector should select the kernel named kernel");
        assert_eq!(selected.name, "kernel");
        assert_eq!(selected.address, 0x180);

        let err = select_cubin_kernel(&parsed, "missing")
            .expect_err("missing requested kernel should fail");
        assert_eq!(err, "No kernel named 'missing' found in CUBIN");
    }

    #[test]
    fn sass_lifter_cubin_frontend_rejects_malformed_input() {
        let err = lift_cubin_to_ptx(b"not an elf", SassLiftOptions::default())
            .expect_err("malformed CUBIN should fail");

        assert!(err.contains("Failed to parse CUBIN"), "{err}");
    }

    fn test_cubin_kernel(name: &str, address: u64) -> CubinKernel {
        CubinKernel {
            name: name.to_string(),
            address,
            size: 0,
            code: Vec::new(),
            num_registers: 0,
            shared_mem_size: 0,
            const_mem_size: 0,
            local_mem_size: 0,
            max_threads_per_block: 0,
            sm_version: 120,
        }
    }

    #[test]
    fn sass_lifter_emits_complete_module_for_sm120_text() {
        let mut s2r = EnhancedSassInstruction::new("S2R".to_string(), 0x0);
        s2r.opcode_class = SassOpcodeClass::SpecialRegRead;
        s2r.data_type = Some(SassDataType::U32);
        s2r.dest_operands.push(reg(0));
        s2r.src_operands
            .push(SassOperand::SpecialRegister("SR_TID.X".to_string()));

        let mut fadd = EnhancedSassInstruction::new("FADD".to_string(), 0x10);
        fadd.opcode_class = SassOpcodeClass::FloatArithmetic;
        fadd.data_type = Some(SassDataType::F32);
        fadd.dest_operands.push(reg(1));
        fadd.src_operands.push(reg(2));
        fadd.src_operands.push(reg(3));

        let mut exit = EnhancedSassInstruction::new("EXIT".to_string(), 0x20);
        exit.opcode_class = SassOpcodeClass::Exit;

        let result = lift_instructions_to_ptx(
            &[s2r, fadd, exit],
            &SassLiftOptions {
                sm_version: 120,
                kernel_name: "sm120_kernel".to_string(),
                include_sass_comments: true,
                emit_unsupported_comments: true,
            },
        );

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains(".version 8.7"));
        assert!(result.ptx.contains(".target sm_120"));
        assert!(result.ptx.contains(".visible .entry sm120_kernel()"));
        assert!(result.ptx.contains(".reg .b32 %r<4>;"));
        assert!(result.ptx.contains("L_0000:"));
        assert!(result.ptx.contains("mov.u32 %r0, %tid.x;"));
        assert!(result.ptx.contains("add.f32 %r1, %r2, %r3;"));
        assert!(result.ptx.contains("ret;"));
    }

    #[test]
    fn sass_lifter_normalizes_load_store_and_constant_memory() {
        let mut ldg = EnhancedSassInstruction::new("LDG".to_string(), 0x0);
        ldg.opcode_class = SassOpcodeClass::GlobalLoad;
        ldg.memory_space = Some(SassMemorySpace::Global);
        ldg.data_type = Some(SassDataType::U32);
        ldg.dest_operands.push(reg(0));
        ldg.src_operands.push(mem(2, 16));

        let mut stg = EnhancedSassInstruction::new("STG".to_string(), 0x10);
        stg.opcode_class = SassOpcodeClass::GlobalStore;
        stg.memory_space = Some(SassMemorySpace::Global);
        stg.data_type = Some(SassDataType::F32);
        stg.dest_operands.push(mem(4, 0));
        stg.src_operands.push(reg(1));

        let mut ldc = EnhancedSassInstruction::new("LDC".to_string(), 0x20);
        ldc.opcode_class = SassOpcodeClass::ConstantLoad;
        ldc.memory_space = Some(SassMemorySpace::Constant);
        ldc.data_type = Some(SassDataType::U64);
        ldc.dest_operands.push(reg(6));
        ldc.src_operands.push(SassOperand::ConstantBank {
            bank: 0,
            offset: 0x160,
        });

        let result = lift_instructions_to_ptx(&[ldg, stg, ldc], &SassLiftOptions::default());

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains("ld.global.u32 %r0, [%r2+16];"));
        assert!(result.ptx.contains("st.global.f32 [%r4], %r1;"));
        assert!(result.ptx.contains("mov.u64 %r6, 0;"));
    }

    #[test]
    fn sass_lifter_maps_sm120_launch_constant_bank_metadata() {
        let text = r#"Function : launch_metadata
        /*0000*/                   LDC R1, c[0x0][0x37c]                  ?trans1;          /* 0x0000df00ff017b82 */
        /*0010*/                   LDCU UR6, c[0x0][0x370]       &wr=0x0  ?trans7;          /* 0x00006e00ff0677ac */
        /*0020*/                   LDC R5, c[0x0][0x374]         &wr=0x0  ?trans8;          /* 0x0000dd00ff057b82 */
        /*0030*/                   LDC R0, c[0x0][0x378]         &wr=0x0  ?trans2;          /* 0x0000de00ff007b82 */
        /*0040*/                   LDC R2, c[0x0][0x360]         &wr=0x1  ?trans2;          /* 0x0000d800ff027b82 */
        /*0050*/                   LDC R3, c[0x0][0x364]         &wr=0x1  ?trans2;          /* 0x0000d900ff037b82 */
        /*0060*/                   LDC R4, c[0x0][0x368]         &wr=0x1  ?trans2;          /* 0x0000da00ff047b82 */
"#;

        let result = lift_sass_text_to_ptx(
            text,
            SassLiftOptions {
                include_sass_comments: false,
                ..SassLiftOptions::default()
            },
        )
        .expect("SM120 launch metadata constant bank reads should lift");

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains("mov.u32 %r1, 0;"));
        assert!(result.ptx.contains("mov.u32 %ur6, %nctaid.x;"));
        assert!(result.ptx.contains("mov.u32 %r5, %nctaid.y;"));
        assert!(result.ptx.contains("mov.u32 %r0, %nctaid.z;"));
        assert!(result.ptx.contains("mov.u32 %r2, %ntid.x;"));
        assert!(result.ptx.contains("mov.u32 %r3, %ntid.y;"));
        assert!(result.ptx.contains("mov.u32 %r4, %ntid.z;"));
        assert!(!result.ptx.contains(".param"));
        assert!(!result.ptx.contains("c[0x0]"));
    }

    #[test]
    fn sass_lifter_expands_cuda_param_abi_from_sm120_constant_bank_reads() {
        let text = r#"Function : kimi_params
        /*0000*/                   LDC.64 R12, c[0x0][0x380]                           &wr=0x3                  ?trans1;           /* 0x0000e000ff0c8b82 */
        /*0010*/                   LDC.64 R4, c[0x0][0x388]                            &wr=0x1                  ?trans1;           /* 0x0000e200ff047b82 */
        /*0020*/                   LDC.64 R6, c[0x0][0x390]                            &wr=0x0                  ?trans8;           /* 0x0000e400ff068b82 */
        /*0030*/                   LDCU.64 UR14, c[0x0][0x398]                         &wr=0x1                  ?trans1;           /* 0x00007300ff0e77ac */
        /*0040*/                   LDCU.64 UR4, c[0x0][0x3a0]                          &wr=0x2                  ?trans1;           /* 0x00007400ff0477ac */
"#;

        let result = lift_sass_text_to_ptx(
            text,
            SassLiftOptions {
                include_sass_comments: false,
                ..SassLiftOptions::default()
            },
        )
        .expect("Kimi-style CUDA parameter constant bank reads should lift");

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains(".param .u64 out"));
        assert!(result.ptx.contains(".param .u64 in"));
        assert!(result.ptx.contains(".param .u64 param2"));
        assert!(result.ptx.contains(".param .u64 param3"));
        assert!(result.ptx.contains(".param .u64 param4"));
        assert!(result.ptx.contains("ld.param.u64 %rd12, [out];"));
        assert!(result.ptx.contains("ld.param.u64 %rd4, [in];"));
        assert!(result.ptx.contains("ld.param.u64 %rd6, [param2];"));
        assert!(result
            .ptx
            .contains("ld.param.u32 %ur14, [param3];\n    ld.param.u32 %ur15, [param3+4];"));
        assert!(result
            .ptx
            .contains("ld.param.u32 %ur4, [param4];\n    ld.param.u32 %ur5, [param4+4];"));
        assert!(!result.ptx.contains("c[0x0]"));
    }

    #[test]
    fn sass_lifter_lowers_sm120_ptxas_followup_bucket_ops() {
        let text = r#"Function : ptxas_followup
        /*0000*/                   ISETP.GE.S64.AND P0, PT, R8, UR4, PT                &req={2}                 ?WAIT14_END_GROUP; /* 0x0000000408007c0c */
        /*0010*/                   CS2R R4, SRZ                                                                 ?WAIT4_END_GROUP;  /* 0x0000000000047805 */
        /*0020*/                   SHF.R.U64 R19, R2, 0x5, R3                                                   ?trans1;           /* 0x0000000502137819 */
        /*0030*/                   FMNMX.FTZ R9, |R5|, |R4|, !PT                       &req={2}                 ?WAIT5_END_GROUP;  /* 0x4000000405097209 */
"#;

        let result = lift_sass_text_to_ptx(
            text,
            SassLiftOptions {
                include_sass_comments: false,
                ..SassLiftOptions::default()
            },
        )
        .expect("SM120 ptxas follow-up bucket ops should lift");

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains("setp.ge.s32 %p0, %r8, %ur4;"));
        assert!(result.ptx.contains("mov.u32 %r4, 0;"));
        assert!(result.ptx.contains("shf.r.clamp.b32 %r19, %r2, %r3, 5;"));
        assert!(result.ptx.contains(".reg .b32 %r<22>;"));
        assert!(result
            .ptx
            .contains("abs.f32 %r21, %r5;\n    abs.f32 %r9, %r4;\n    max.f32 %r9, %r21, %r9;"));
        assert!(!result.ptx.contains("|R"));
        assert!(!result.ptx.contains("SRZ"));
    }

    #[test]
    fn sass_lifter_maps_fmnmx_static_selector_to_min_or_max() {
        let text = r#"Function : fmnmx_selector
        /*0000*/                   FMNMX R4, R1, R2, PT                                ?WAIT5_END_GROUP;  /* 0x0000000201047209 */
        /*0010*/                   FMNMX R5, R1, R2, !PT                               ?WAIT5_END_GROUP;  /* 0x0000000201057209 */
"#;

        let result = lift_sass_text_to_ptx(
            text,
            SassLiftOptions {
                include_sass_comments: false,
                ..SassLiftOptions::default()
            },
        )
        .expect("FMNMX static min/max selector should lift");

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains("min.f32 %r4, %r1, %r2;"));
        assert!(result.ptx.contains("max.f32 %r5, %r1, %r2;"));
    }

    #[test]
    fn sass_lifter_formats_zero_register_as_float_literal_in_float_ops() {
        let text = r#"Function : float_zero
        /*0000*/                   FMNMX R4, RZ, R4, PT                                ?WAIT5_END_GROUP;  /* 0x00000004ff047209 */
        /*0010*/                   FADD R8, RZ, R8                                     ?trans1;           /* 0x00000008ff087221 */
        /*0020*/                   FMUL R5, RZ, R15                                    ?trans1;           /* 0x0000000fff057220 */
"#;

        let result = lift_sass_text_to_ptx(
            text,
            SassLiftOptions {
                include_sass_comments: false,
                ..SassLiftOptions::default()
            },
        )
        .expect("float zero register operands should lift");

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains("min.f32 %r4, 0f00000000, %r4;"));
        assert!(result.ptx.contains("add.f32 %r8, 0f00000000, %r8;"));
        assert!(result.ptx.contains("mul.f32 %r5, 0f00000000, %r15;"));
        assert!(!result.ptx.contains("min.f32 %r4, 0, %r4;"));
        assert!(!result.ptx.contains("add.f32 %r8, 0, %r8;"));
        assert!(!result.ptx.contains("mul.f32 %r5, 0, %r15;"));
    }

    #[test]
    fn sass_lifter_canonicalizes_abs_reuse_float_operands_for_parser() {
        let mut fmnmx = EnhancedSassInstruction::new("FMNMX".to_string(), 0x0);
        fmnmx.opcode_class = SassOpcodeClass::FloatArithmetic;
        fmnmx.data_type = Some(SassDataType::F32);
        fmnmx.dest_operands.push(reg(9));
        fmnmx
            .src_operands
            .push(SassOperand::Label("|R5|.reuse".to_string()));
        fmnmx
            .src_operands
            .push(SassOperand::Label("|R4|".to_string()));
        fmnmx
            .src_operands
            .push(SassOperand::Label("!PT".to_string()));

        let mut fadd = EnhancedSassInstruction::new("FADD".to_string(), 0x10);
        fadd.opcode_class = SassOpcodeClass::FloatArithmetic;
        fadd.data_type = Some(SassDataType::F32);
        fadd.dest_operands.push(reg(4));
        fadd.src_operands
            .push(SassOperand::Label("|R0|".to_string()));
        fadd.src_operands
            .push(SassOperand::Label("-RZ".to_string()));

        let mut fsetp = EnhancedSassInstruction::new("FSETP".to_string(), 0x20);
        fsetp.opcode_class = SassOpcodeClass::FloatComparison;
        fsetp.data_type = Some(SassDataType::F32);
        fsetp.modifiers.push("GT".to_string());
        fsetp.dest_operands.push(pred(0));
        fsetp
            .src_operands
            .push(SassOperand::Register(SassRegister::new("PT", 7)));
        fsetp
            .src_operands
            .push(SassOperand::Label("|R31|".to_string()));
        fsetp
            .src_operands
            .push(SassOperand::Register(SassRegister::new("RZ", 255)));
        fsetp
            .src_operands
            .push(SassOperand::Register(SassRegister::new("PT", 7)));

        let result = lift_instructions_to_ptx(
            &[fmnmx, fadd, fsetp],
            &SassLiftOptions {
                include_sass_comments: false,
                ..SassLiftOptions::default()
            },
        );

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(!result.ptx.contains("|R"), "{}", result.ptx);
        assert!(!result.ptx.contains(".reuse"), "{}", result.ptx);
        assert!(result.ptx.contains("abs.f32"), "{}", result.ptx);
        assert!(result.ptx.contains(", %r5;"), "{}", result.ptx);
        assert!(result.ptx.contains("abs.f32 %r9, %r4;"));
        assert!(result.ptx.contains("add.f32 %r4,"), "{}", result.ptx);
        assert!(result.ptx.contains("setp.gt.f32 %p0,"), "{}", result.ptx);
        assert!(result.ptx.contains(", 0f00000000;"), "{}", result.ptx);
        ptx_parser::parse_module_checked(&result.ptx)
            .expect("absolute/reuse lifted PTX syntax should parse");
    }

    #[test]
    fn sass_lifter_preserves_predicates_and_branch_targets() {
        let mut isetp = EnhancedSassInstruction::new("ISETP".to_string(), 0x0);
        isetp.opcode_class = SassOpcodeClass::IntegerComparison;
        isetp.data_type = Some(SassDataType::S32);
        isetp.modifiers.push("LT".to_string());
        isetp.dest_operands.push(pred(0));
        isetp.src_operands.push(reg(1));
        isetp.src_operands.push(reg(2));

        let mut bra = EnhancedSassInstruction::new("BRA".to_string(), 0x10);
        bra.opcode_class = SassOpcodeClass::Branch;
        bra.predicate = Some(pred(0));
        bra.src_operands.push(SassOperand::Immediate(0x40));

        let mut iadd = EnhancedSassInstruction::new("IADD".to_string(), 0x20);
        iadd.opcode_class = SassOpcodeClass::IntegerArithmetic;
        iadd.data_type = Some(SassDataType::S32);
        iadd.dest_operands.push(reg(3));
        iadd.src_operands.push(reg(3));
        iadd.src_operands.push(SassOperand::Immediate(1));

        let mut ret = EnhancedSassInstruction::new("RET".to_string(), 0x40);
        ret.opcode_class = SassOpcodeClass::Exit;

        let result =
            lift_instructions_to_ptx(&[isetp, bra, iadd, ret], &SassLiftOptions::default());

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains("setp.lt.s32 %p0, %r1, %r2;"));
        assert!(result.ptx.contains("@%p0 bra L_0040;"));
        assert!(result.ptx.contains("add.s32 %r3, %r3, 1;"));
        assert!(result.ptx.contains("L_0040:"));
    }

    #[test]
    fn sass_lifter_lowers_call_rel_noinc_to_synthetic_continuation() {
        let text = r#"Function : call_stub
        /*0000*/                   CALL.REL.NOINC 0x40 ;
        /*0010*/                   IADD R2, R2, 1 ;
        /*0020*/                   EXIT ;
        /*0040*/                   IADD R3, R3, 1 ;
        /*0050*/                   RET.REL.NODEC R6 0x0 ;
"#;

        let result = lift_sass_text_to_ptx(
            text,
            SassLiftOptions {
                include_sass_comments: false,
                ..SassLiftOptions::default()
            },
        )
        .expect("CALL/RET text should parse");

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains(".reg .b32 %r<7>;"), "{}", result.ptx);
        assert!(result.ptx.contains(".reg .pred %p<1>;"));
        assert!(result.ptx.contains("mov.u32 %r4, 0;"));
        assert!(result.ptx.contains(
            "setp.eq.u32 %p0, %r4, 0;\n    @%p0 mov.u32 %r6, 1;\n    add.u32 %r4, %r4, 1;\n    bra L_0040;\nL_callret_0000:"
        ));
        assert!(result.ptx.contains(
            "setp.eq.u32 %p0, %r4, 0;\n    @%p0 ret;\n    sub.u32 %r4, %r4, 1;\n    mov.u32 %r5, 0;\n    setp.eq.u32 %p0, %r4, 0;\n    @%p0 mov.u32 %r5, %r6;\n    setp.eq.u32 %p0, %r5, 1;\n    @%p0 bra L_callret_0000;"
        ));
        assert!(!result.ptx.contains("unsupported SASS CALL"));
        ptx_parser::parse_module_checked(&result.ptx).expect("lifted CALL PTX should parse");
    }

    #[test]
    fn sass_lifter_lowers_nested_local_calls_with_stack_slots() {
        let text = r#"Function : nested_call_stub
        /*0000*/                   CALL.REL.NOINC 0x40 ;
        /*0010*/                   EXIT ;
        /*0040*/                   CALL.REL.NOINC 0x80 ;
        /*0050*/                   RET.REL.NODEC R6 0x0 ;
        /*0080*/                   IADD R1, R1, 1 ;
        /*0090*/                   RET.REL.NODEC R6 0x0 ;
"#;

        let result = lift_sass_text_to_ptx(
            text,
            SassLiftOptions {
                include_sass_comments: false,
                ..SassLiftOptions::default()
            },
        )
        .expect("nested CALL/RET text should parse");

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains(".reg .b32 %r<6>;"), "{}", result.ptx);
        assert!(result.ptx.contains(
            "setp.eq.u32 %p0, %r2, 0;\n    @%p0 mov.u32 %r4, 2;\n    setp.eq.u32 %p0, %r2, 1;\n    @%p0 mov.u32 %r5, 2;\n    add.u32 %r2, %r2, 1;\n    bra L_0080;\nL_callret_0040:"
        ));
        assert!(result.ptx.contains("@%p0 bra L_callret_0000;"));
        assert!(result.ptx.contains("@%p0 bra L_callret_0040;"));
        assert!(!result.ptx.contains("unsupported SASS CALL"));
        ptx_parser::parse_module_checked(&result.ptx).expect("nested lifted CALL PTX should parse");
    }

    #[test]
    fn sass_lifter_emits_static_jmx_as_branch() {
        let mut jmx = EnhancedSassInstruction::new("JMX".to_string(), 0x10);
        jmx.opcode_class = SassOpcodeClass::ConditionalBranch;
        jmx.src_operands.push(SassOperand::Address(0x40));

        let mut ret = EnhancedSassInstruction::new("RET".to_string(), 0x40);
        ret.opcode_class = SassOpcodeClass::Exit;

        let result = lift_instructions_to_ptx(&[jmx, ret], &SassLiftOptions::default());

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains("L_0040:\n"));
        assert!(result.ptx.contains("bra L_0040;"));
    }

    #[test]
    fn sass_lifter_expands_three_source_iadd3_with_scratch_register() {
        let mut iadd3 = EnhancedSassInstruction::new("IADD3".to_string(), 0x0);
        iadd3.opcode_class = SassOpcodeClass::IntegerArithmetic;
        iadd3.data_type = Some(SassDataType::S32);
        iadd3.dest_operands.push(reg(2));
        iadd3.src_operands.push(reg(9));
        iadd3.src_operands.push(reg(1));
        iadd3.src_operands.push(reg(4));

        let result = lift_instructions_to_ptx(&[iadd3], &SassLiftOptions::default());

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains(".reg .b32 %r<11>;"));
        assert!(result.ptx.contains("add.s32 %r10, %r9, %r1;"));
        assert!(result.ptx.contains("add.s32 %r2, %r10, %r4;"));
    }

    #[test]
    fn sass_lifter_emits_generic_lop3_lut() {
        let mut lop3 = EnhancedSassInstruction::new("LOP3".to_string(), 0x0);
        lop3.data_type = Some(SassDataType::B32);
        lop3.dest_operands.push(reg(0));
        lop3.src_operands.push(reg(1));
        lop3.src_operands.push(reg(2));
        lop3.src_operands.push(reg(3));
        lop3.src_operands.push(SassOperand::Immediate(0xca));

        let result = lift_instructions_to_ptx(&[lop3], &SassLiftOptions::default());

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains("lop3.b32 %r0, %r1, %r2, %r3, 0xca;"));
        assert!(!result.ptx.contains("and.b32"));
    }

    #[test]
    fn sass_lifter_emits_parser_compatible_roundtrip_syntax() {
        let mut iadd = EnhancedSassInstruction::new("IADD3".to_string(), 0x0);
        iadd.opcode_class = SassOpcodeClass::IntegerArithmetic;
        iadd.data_type = Some(SassDataType::U32);
        iadd.dest_operands.push(reg(3));
        iadd.src_operands.push(reg(3));
        iadd.src_operands
            .push(SassOperand::Label("!PT".to_string()));

        let mut fmul = EnhancedSassInstruction::new("FMUL".to_string(), 0x10);
        fmul.opcode_class = SassOpcodeClass::FloatArithmetic;
        fmul.data_type = Some(SassDataType::F32);
        fmul.dest_operands.push(reg(5));
        fmul.src_operands.push(SassOperand::FloatImmediate(0.5));
        fmul.src_operands.push(reg(15));

        let mut ldc = EnhancedSassInstruction::new("LDC".to_string(), 0x20);
        ldc.opcode_class = SassOpcodeClass::ConstantLoad;
        ldc.memory_space = Some(SassMemorySpace::Constant);
        ldc.data_type = Some(SassDataType::U32);
        ldc.dest_operands.push(reg(10));
        ldc.src_operands.push(SassOperand::ConstantBank {
            bank: 4,
            offset: 0x38,
        });

        let mut lop3 = EnhancedSassInstruction::new("LOP3".to_string(), 0x30);
        lop3.data_type = Some(SassDataType::B32);
        lop3.dest_operands.push(reg(0));
        lop3.src_operands.push(reg(1));
        lop3.src_operands.push(reg(2));
        lop3.src_operands.push(reg(3));
        lop3.src_operands.push(SassOperand::Immediate(0xca));

        let result = lift_instructions_to_ptx(
            &[iadd, fmul, ldc, lop3],
            &SassLiftOptions {
                include_sass_comments: false,
                ..SassLiftOptions::default()
            },
        );

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains("add.u32 %r3, %r3, 0;"));
        assert!(result.ptx.contains("mul.f32 %r5, 0f3f000000, %r15;"));
        assert!(result.ptx.contains("mov.u32 %r10, 0;"));
        assert!(result.ptx.contains("lop3.b32 %r0, %r1, %r2, %r3, 0xca;"));
        ptx_parser::parse_module_checked(&result.ptx)
            .expect("Kimi-style lifted PTX syntax should parse for roundtrip checks");
    }

    #[test]
    fn sass_lifter_emits_generic_lop_lut_when_truth_table_is_present() {
        let mut lop = EnhancedSassInstruction::new("LOP".to_string(), 0x0);
        lop.opcode_class = SassOpcodeClass::IntegerLogical;
        lop.data_type = Some(SassDataType::B32);
        lop.dest_operands.push(reg(0));
        lop.src_operands.push(reg(1));
        lop.src_operands.push(reg(2));
        lop.src_operands.push(reg(3));
        lop.src_operands.push(SassOperand::Immediate(0xe2));

        let result = lift_instructions_to_ptx(&[lop], &SassLiftOptions::default());

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains("lop3.b32 %r0, %r1, %r2, %r3, 0xe2;"));
        assert!(!result.ptx.contains("unsupported SASS LOP"));
    }

    #[test]
    fn sass_lifter_lifts_kimi_binary_lop_text_sample() {
        let text = r#"Function : kimi_lop
        /*0ca0*/                   LOP R48, R114, R243 ;
        /*0cb0*/                   EXIT ;
"#;

        let result =
            lift_sass_text_to_ptx(text, SassLiftOptions::default()).expect("LOP text should lift");

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains(".reg .b32 %r<244>;"));
        assert!(result.ptx.contains("and.b32 %r48, %r114, %r243;"));
        assert!(!result.ptx.contains("unsupported SASS LOP"));
    }

    #[test]
    fn sass_lifter_lifts_decoded_binary_lop_with_spurious_third_source() {
        let mut lop = EnhancedSassInstruction::new("LOP".to_string(), 0x0ca0);
        lop.opcode_class = SassOpcodeClass::IntegerLogical;
        lop.data_type = Some(SassDataType::B32);
        lop.dest_operands.push(reg(48));
        lop.src_operands.push(reg(114));
        lop.src_operands.push(reg(243));
        lop.src_operands.push(reg(7));

        let result = lift_instructions_to_ptx(&[lop], &SassLiftOptions::default());

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains("and.b32 %r48, %r114, %r243;"));
        assert!(!result.ptx.contains("%r7"));
    }

    #[test]
    fn sass_lifter_respects_binary_lop_or_and_xor_modifiers() {
        let mut lop_or = EnhancedSassInstruction::new("LOP".to_string(), 0x0);
        lop_or.modifiers.push("OR".to_string());
        lop_or.dest_operands.push(reg(1));
        lop_or.src_operands.push(reg(2));
        lop_or.src_operands.push(reg(3));

        let mut lop_xor = EnhancedSassInstruction::new("LOP".to_string(), 0x10);
        lop_xor.modifiers.push("XOR".to_string());
        lop_xor.dest_operands.push(reg(4));
        lop_xor.src_operands.push(reg(5));
        lop_xor.src_operands.push(reg(6));

        let result = lift_instructions_to_ptx(&[lop_or, lop_xor], &SassLiftOptions::default());

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains("or.b32 %r1, %r2, %r3;"));
        assert!(result.ptx.contains("xor.b32 %r4, %r5, %r6;"));
    }

    #[test]
    fn sass_lifter_emits_rounded_ffma() {
        let mut ffma = EnhancedSassInstruction::new("FFMA".to_string(), 0x0);
        ffma.opcode_class = SassOpcodeClass::FloatArithmetic;
        ffma.data_type = Some(SassDataType::F32);
        ffma.dest_operands.push(reg(0));
        ffma.src_operands.push(reg(1));
        ffma.src_operands.push(reg(2));
        ffma.src_operands.push(reg(3));

        let result = lift_instructions_to_ptx(&[ffma], &SassLiftOptions::default());

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains("fma.rn.f32 %r0, %r1, %r2, %r3;"));
    }

    #[test]
    fn sass_lifter_reports_psetp_instead_of_emitting_pred_setp() {
        let mut psetp = EnhancedSassInstruction::new("PSETP".to_string(), 0x0);
        psetp.data_type = Some(SassDataType::Pred);
        psetp.dest_operands.push(pred(0));
        psetp.src_operands.push(pred(1));
        psetp.src_operands.push(pred(2));

        let result = lift_instructions_to_ptx(&[psetp], &SassLiftOptions::default());

        assert_eq!(result.diagnostics.len(), 1);
        assert_eq!(result.diagnostics[0].opcode, "PSETP");
        assert_eq!(
            result.diagnostics[0].message,
            "predicate set lifting is not implemented"
        );
        assert!(result.ptx.contains(
            "unsupported SASS PSETP at 0x0000: predicate set lifting is not implemented"
        ));
        assert!(!result.ptx.contains("setp.eq.pred"));
    }

    #[test]
    fn sass_lifter_reports_unsupported_tensor_instruction() {
        let hmma = EnhancedSassInstruction::new("HMMA".to_string(), 0x120);

        let result = lift_instructions_to_ptx(&[hmma], &SassLiftOptions::default());

        assert_eq!(result.diagnostics.len(), 1);
        assert_eq!(result.diagnostics[0].opcode, "HMMA");
        assert_eq!(
            result.diagnostics[0].message,
            "tensor instruction lifting is not implemented"
        );
        assert!(result.ptx.contains("unsupported SASS HMMA at 0x0120"));
    }

    #[test]
    fn sass_lifter_diagnostics_keep_original_instruction_text() {
        let mut tex = EnhancedSassInstruction::new("TEX".to_string(), 0x80);
        tex.instruction_text =
            "/*0080*/ TEX R4, R5, R6, 0x2 ; /* 0x4000000000600504 */".to_string();

        let result = lift_instructions_to_ptx(&[tex], &SassLiftOptions::default());

        assert_eq!(result.diagnostics.len(), 1);
        assert_eq!(
            result.diagnostics[0].instruction_text,
            "/*0080*/ TEX R4, R5, R6, 0x2 ; /* 0x4000000000600504 */"
        );
    }

    #[test]
    fn sass_lifter_lifts_sm120_scalar_and_warp_bucket_ops() {
        let text = r#"Function : bucket_ops
        /*0000*/                   IABS R2, R18 ;
        /*0010*/                   I2F.RP R14, R2 ;
        /*0020*/                   UI2F.U32.RP UR4, UR9 ;
        /*0030*/                   UI2FP.F32.S32 UR5, UR10 ;
        /*0040*/                   MUFU.RCP R3, R4 ;
        /*0050*/                   BSSY.RECONVERGENT B0, 0x90 ;
        /*0060*/                   BSYNC.RECONVERGENT B0 ;
        /*0070*/                   SHFL.BFLY PT, R5, R8, 0x10, 0x1f ;
        /*0080*/              @P0  FRND.TRUNC R0, R9 ;
        /*0090*/                   EXIT ;
"#;

        let result = lift_sass_text_to_ptx(text, SassLiftOptions::default())
            .expect("SM120 bucket ops should lift");

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains("abs.s32 %r2, %r18;"));
        assert!(result.ptx.contains("cvt.rp.f32.s32 %r14, %r2;"));
        assert!(result.ptx.contains("cvt.rp.f32.u32 %ur4, %ur9;"));
        assert!(result.ptx.contains("cvt.rn.f32.s32 %ur5, %ur10;"));
        assert!(result.ptx.contains("rcp.approx.ftz.f32 %r3, %r4;"));
        assert!(result.ptx.contains("// bssy reconvergence marker;"));
        assert!(result.ptx.contains("// bsync reconvergence marker;"));
        assert!(result
            .ptx
            .contains("shfl.sync.bfly.b32 %r5, %r8, 0x10, 0x1f, 0xffffffff;"));
        assert!(result.ptx.contains("@%p0 cvt.rzi.f32.f32 %r0, %r9;"));
    }

    #[test]
    fn sass_lifter_lifts_predicate_lop3_lut_with_scratch() {
        let text = r#"Function : pred_lop3
        /*0000*/                   LOP3.LUT P0, R6, R0, 0x1f, RZ, 0xc0, !PT ;
        /*0010*/                   EXIT ;
"#;

        let result = lift_sass_text_to_ptx(text, SassLiftOptions::default())
            .expect("predicate LOP3 LUT should lift");

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains(".reg .b32 %r<8>;"));
        assert!(result.ptx.contains("lop3.b32 %r7, %r6, %r0, 31, 0xc0;"));
        assert!(result.ptx.contains("setp.ne.u32 %p0, %r7, 0;"));
    }

    #[test]
    fn sass_lifter_lowers_lop3_odd_predicate_before_generic_predicate_lut() {
        let text = r#"Function : odd_pred
        /*0000*/                   LOP3.LUT P0, RZ, R7, 0x1, RZ, 0xc0, !PT ;
        /*0010*/              @!P0 LOP3.LUT R9, R9, 0x9e3779b9, RZ, 0x3c, !PT ;
        /*0020*/                   EXIT ;
"#;

        let result = lift_sass_text_to_ptx(text, SassLiftOptions::default())
            .expect("odd predicate LOP3 should lift");

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains("and.b32 %r10, %r7, 1;"));
        assert!(result.ptx.contains("setp.ne.u32 %p0, %r10, 0;"));
        assert!(!result.ptx.contains("lop3.b32 %r10, 0, %r7, 1, 0xc0;"));
        assert!(result.ptx.contains("@!%p0 xor.b32 %r9, %r9, 2654435769;"));
    }

    #[test]
    fn sass_lifter_lifts_sm120_uniform_and_permute_bucket_ops() {
        let text = r#"Function : uniform_bucket_ops
        /*0000*/                   UIMAD UR4, UR4, UR6, URZ ;
        /*0010*/                   UIADD3 UR5, UPT, UPT, UR4, UR6, URZ ;
        /*0020*/                   ULOP3.LUT UR6, URZ, UR4, URZ, 0x33, !UPT ;
        /*0030*/                   PRMT R9, R16, 0x7604, R9 ;
        /*0040*/                   EXIT ;
"#;

        let result = lift_sass_text_to_ptx(text, SassLiftOptions::default())
            .expect("uniform SM120 bucket ops should lift");

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains("mad.lo.u32 %ur4, %ur4, %ur6, 0;"));
        assert!(result.ptx.contains("add.u32 %ur5, %ur4, %ur6;"));
        assert!(result.ptx.contains("add.u32 %ur5, %ur5, 0;"));
        assert!(result.ptx.contains("lop3.b32 %ur6, 0, %ur4, 0, 0x33;"));
        assert!(result.ptx.contains("prmt.b32 %r9, %r16, 30212, %r9;"));
    }

    #[test]
    fn sass_lifter_lifts_sm120_half_and_predicate_bucket_ops() {
        let text = r#"Function : half_pred_bucket_ops
        /*0270*/                   PLOP3.LUT P0, PT, P0, P1, PT, 0xf8, 0x8f ?WAIT13_END_GROUP; /* 0x00000000008f781c */
        /*0430*/                   F2FP.F16.F32.PACK_AB R0, R7, R12 ?WAIT4_END_GROUP; /* 0x0000000c0700723e */
        /*06f0*/                   F2FP.F16.F32.PACK_AB R10, RZ, R10 ?WAIT4_END_GROUP; /* 0x0000000aff0a723e */
        /*0700*/                   F2FP.F16.F32.PACK_AB R5, R5, R0 &req={0} ?WAIT5_END_GROUP; /* 0x000000000505723e */
        /*0770*/                   F2FP.F16.F32.PACK_AB R2, RZ, R0 &req={1,0} ?WAIT5_END_GROUP; /* 0x00000000ff02723e */
        /*0a90*/                   F2FP.F16.F32.PACK_AB R11, RZ, R10 &req={2} ?WAIT5_END_GROUP; /* 0x0000000aff0b723e */
        /*0b40*/                   HADD2.F32 R16, -RZ, R14.H0_H0 &req={2} ?WAIT5_END_GROUP; /* 0x2000000eff107230 */
        /*0b40*/                   HADD2.F32 R18, -RZ, R14.H0_H0 &req={3} ?WAIT5_END_GROUP; /* 0x2000000eff127230 */
        /*0b40*/                   HADD2.F32 R20, -RZ, R14.H0_H0 &req={2} ?WAIT5_END_GROUP; /* 0x2000000eff147230 */
        /*0b60*/                   F2FP.F16.F32.PACK_AB R16, RZ, R16 ?WAIT5_END_GROUP; /* 0x00000010ff10723e */
        /*0b60*/                   F2FP.F16.F32.PACK_AB R18, RZ, R18 ?WAIT5_END_GROUP; /* 0x00000012ff12723e */
        /*0d50*/                   F2FP.F16.F32.PACK_AB R11, RZ, R10 &req={2} ?WAIT5_END_GROUP; /* 0x0000000aff0b723e */
        /*0e30*/                   F2FP.F16.F32.PACK_AB R12, RZ, R12 ?WAIT5_END_GROUP; /* 0x0000000cff0c723e */
        /*0e40*/                   F2FP.F16.F32.PACK_AB R12, RZ, R12 ?WAIT5_END_GROUP; /* 0x0000000cff0c723e */
        /*0ff0*/                   F2FP.F16.F32.PACK_AB R6, RZ, R6 &req={3} ?WAIT5_END_GROUP; /* 0x00000006ff06723e */
        /*10a0*/               @P0 HADD2.F32 R0, -RZ, R2.H0_H0 &req={3} ?WAIT5_END_GROUP; /* 0x20000002ff000230 */
        /*10a0*/               @P3 HADD2.F32 R0, -RZ, R2.H0_H0 &req={3} ?WAIT5_END_GROUP; /* 0x20000002ff003230 */
        /*10c0*/                   F2FP.F16.F32.PACK_AB R0, RZ, R0 ?WAIT5_END_GROUP; /* 0x00000000ff00723e */
        /*10d0*/                   F2FP.F16.F32.PACK_AB R0, RZ, R0 ?WAIT5_END_GROUP; /* 0x00000000ff00723e */
        /*10e0*/                   EXIT ;
"#;

        let result = lift_sass_text_to_ptx(text, SassLiftOptions::default())
            .expect("SM120 half and predicate bucket ops should lift");

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains(".reg .b32 %r<22>;"));
        assert!(result
            .ptx
            .contains("cvt.rn.f16x2.f32 %r18, 0f00000000, %r18;"));
        assert!(result.ptx.contains("mov.b32 %r21, 0;"));
        assert!(result.ptx.contains("add.rn.f16x2 %r20, %r21, %r14;"));
        assert!(result
            .ptx
            .contains("@%p0 mov.b32 %r21, 0;\n    @%p0 add.rn.f16x2 %r0, %r21, %r2;"));
        assert!(result
            .ptx
            .contains("@%p3 mov.b32 %r21, 0;\n    @%p3 add.rn.f16x2 %r0, %r21, %r2;"));
        assert!(result.ptx.contains("or.pred %p0, %p0, %p1;"));
    }

    #[test]
    fn sass_lifter_lifts_sm120_uniform_shift_and_minmax_bucket_ops() {
        let text = r#"Function : uniform_shift_minmax_bucket_ops
        /*0090*/                   UVIMNMX.S32 UR5, UR5, UR7, UPT ?trans1; /* 0x000000070505724a */
        /*0110*/                   VIMNMX.S32 R3, R3, UR5, !PT ?WAIT5_END_GROUP; /* 0x0000000503037c48 */
        /*0300*/                   USHF.R.U64 UR8, UR8, 0x5, UR6 ?trans1; /* 0x0000000508087899 */
        /*0310*/                   USHF.R.U32.HI UR6, URZ, 0x5, UR6 ?WAIT3_END_GROUP; /* 0x00000005ff067899 */
        /*0920*/                   VIMNMX.S32 R5, R4, UR5, !PT ?trans1; /* 0x0000000504057c48 */
        /*0a50*/                   VIMNMX.S32 R7, R6, UR5, !PT ?trans1; /* 0x0000000506077c48 */
        /*0b00*/                   EXIT ;
"#;

        let result = lift_sass_text_to_ptx(text, SassLiftOptions::default())
            .expect("SM120 uniform shift and min/max bucket ops should lift");

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains(".reg .b32 %ur<10>;"));
        assert!(result.ptx.contains("min.s32 %ur5, %ur5, %ur7;"));
        assert!(result.ptx.contains("max.s32 %r3, %r3, %ur5;"));
        assert!(result.ptx.contains("max.s32 %r5, %r4, %ur5;"));
        assert!(result.ptx.contains("max.s32 %r7, %r6, %ur5;"));
        assert!(result.ptx.contains("shf.r.clamp.b32 %ur8, %ur8, %ur6, 5;"));
        assert!(result.ptx.contains("shf.r.clamp.b32 %ur6, 0, %ur6, 5;"));
    }

    #[test]
    fn sass_lifter_lifts_kimi_idp_and_fsel_bucket_ops() {
        let text = r#"Function : kimi_idp_fsel
        /*1dc0*/                   IDP.4A.S8.S8 R91, R88, R77.reuse, RZ ?trans1; /* 0x0000004d585b7226 */
        /*0a20*/                   FSEL R8, R8, R9, !P1 ?trans2; /* 0x0000000908087208 */
        /*0ab0*/               @P1 FSEL R10, R31, RZ, P0 ?trans2; /* 0x000000ff1f0a1208 */
        /*1dd0*/                   EXIT ;
"#;

        let result = lift_sass_text_to_ptx(text, SassLiftOptions::default())
            .expect("Kimi IDP/FSEL bucket ops should lift");

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains("dp4a.s32.s32 %r91, %r88, %r77, 0;"));
        assert!(result.ptx.contains("selp.b32 %r8, %r9, %r8, %p1;"));
        assert!(result.ptx.contains("@%p1 selp.b32 %r10, %r31, 0, %p0;"));
    }

    #[test]
    fn sass_lifter_lifts_kimi_descriptor_store_with_offset() {
        let text = r#"Function : kimi_store
        /*1150*/                   ST.E.U16 desc[UR8][R32.64+0x40], R21 &rd=0x0 ?trans1; /* 0x0000401520007985 */
        /*1160*/                   EXIT ;
"#;

        let result = lift_sass_text_to_ptx(text, SassLiftOptions::default())
            .expect("Kimi descriptor store should lift");

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains("st.global.u16 [%rd32+64], %r21;"));
    }

    #[test]
    fn sass_lifter_lifts_kimi_mufu_bucket_ops() {
        let text = r#"Function : kimi_mufu
        /*0460*/                   MUFU.LG2 R10, UR7 &req={3} &wr=0x2 ?trans1; /* 0x00000007000a7d08 */
        /*04e0*/                   MUFU.EX2 R3, R3 &wr=0x1 ?trans3; /* 0x0000000300037308 */
        /*0620*/                   MUFU.SIN R11, R12 &wr=0x2 ?trans1; /* 0x0000000c000b7308 */
        /*0640*/               @P0 MUFU.COS R3, R12 &wr=0x3 ?trans3; /* 0x0000000c00037308 */
        /*0650*/                   EXIT ;
"#;

        let result = lift_sass_text_to_ptx(text, SassLiftOptions::default())
            .expect("Kimi MUFU bucket ops should lift");

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains("lg2.approx.ftz.f32 %r10, %ur7;"));
        assert!(result.ptx.contains("ex2.approx.ftz.f32 %r3, %r3;"));
        assert!(result.ptx.contains("sin.approx.ftz.f32 %r11, %r12;"));
        assert!(result.ptx.contains("@%p0 cos.approx.ftz.f32 %r3, %r12;"));
    }

    #[test]
    fn sass_lifter_lifts_kimi_r2p_bucket_op() {
        let text = r#"Function : kimi_r2p
        /*0460*/                   R2P PR, R9, 0x7e ?trans1; /* 0x0000007e09007804 */
        /*0470*/                   EXIT ;
"#;

        let result =
            lift_sass_text_to_ptx(text, SassLiftOptions::default()).expect("R2P should lift");

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains(".reg .b32 %r<11>;"));
        assert!(result.ptx.contains(".reg .pred %p<7>;"));
        assert!(result.ptx.contains("and.b32 %r10, %r9, 0x2;"));
        assert!(result.ptx.contains("setp.ne.u32 %p1, %r10, 0;"));
        assert!(result.ptx.contains("and.b32 %r10, %r9, 0x40;"));
        assert!(result.ptx.contains("setp.ne.u32 %p6, %r10, 0;"));
        assert!(!result.ptx.contains("%p0, %r10"));
    }

    #[test]
    fn sass_lifter_lifts_kimi_uniform_setp_and_sel_bucket_ops() {
        let text = r#"Function : kimi_uniform_pred
        /*07a0*/                   UISETP.GE.AND UP0, UPT, UR7, URZ, UPT ?WAIT4_END_GROUP; /* 0x000000ff0700728c */
        /*07b0*/                   USEL.64 UR8, UR8, UR6, !UP0 ?WAIT9_END_GROUP; /* 0x0000000608087c87 */
        /*07c0*/                   EXIT ;
"#;

        let result = lift_sass_text_to_ptx(text, SassLiftOptions::default())
            .expect("Kimi uniform predicate/select ops should lift");

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains(".reg .b32 %ur<10>;"));
        assert!(result.ptx.contains(".reg .pred %up<1>;"));
        assert!(result.ptx.contains("setp.ge.u32 %up0, %ur7, 0;"));
        assert!(result
            .ptx
            .contains("selp.u32 %ur8, %ur6, %ur8, %up0;\n    selp.u32 %ur9, %ur7, %ur9, %up0;"));
    }

    #[test]
    fn sass_lifter_lifts_kimi_global_atomic_add_bucket_op() {
        let text = r#"Function : kimi_atomic_add
        /*0310*/                   ATOMG.E.ADD.STRONG.GPU PT, R9, desc[UR6][R8.64], R3 &req={0} &wr=0x2 ?trans1; /* 0x80000003080979a8 */
        /*0320*/                   EXIT ;
"#;

        let result = lift_sass_text_to_ptx(text, SassLiftOptions::default())
            .expect("Kimi global atomic add should lift");

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains("atom.global.add.u32 %r9, [%rd8], %r3;"));
    }

    #[test]
    fn sass_lifter_lifts_kimi_hmul2_bucket_op() {
        let text = r#"Function : kimi_hmul2
        /*0af0*/                   HMUL2 R7, R14, R7.H0_H0 ?WAIT5_END_GROUP; /* 0x200000070e077232 */
        /*0b00*/                   EXIT ;
"#;

        let result = lift_sass_text_to_ptx(text, SassLiftOptions::default())
            .expect("Kimi HMUL2 bucket op should lift");

        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert!(result.ptx.contains("mul.rn.f16x2 %r7, %r14, %r7;"));
    }
}
