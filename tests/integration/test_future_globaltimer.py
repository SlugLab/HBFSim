"""Finite CPU admission/order checks for the read-only diagnostic PTX clock.

Optionally assemble both accepted emitted kernels with the actual ptxas. This
checks compilation only; it does not certify timer resolution or GPU overlap.
"""
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def program(clock='mov.u64', replacement=None, declarations=''):
    operation = replacement or f'{clock} %t0, %globaltimer;'
    return '''.version 9.0
.target sm_120
.address_size 64
.visible .entry kernel(.param .u64 input, .param .u64 output) {
.reg .b64 %addr, %out, %t0, %t1;
.reg .b32 %loaded, %work, %answer;
.reg .pred %active;
''' + declarations + '''
ld.param.u64 %addr, [input];
ld.param.u64 %out, [output];
mov.u32 %work, 7;
setp.eq.u32 %active, %work, 7;
ld.global.u32 %loaded, [%addr];
''' + operation + '''
mad.lo.u32 %work, %work, 3, 5;
@%active ''' + clock + ''' %t1, %globaltimer;
add.u32 %answer, %loaded, %work;
st.global.u32 [%out], %answer;
st.global.u64 [%out+8], %t0;
st.global.u64 [%out+16], %t1;
ret;
}
'''


def main():
    driver = Path(sys.argv[1]).resolve(strict=True)
    ptxas = Path(sys.argv[2]).resolve(strict=True) if len(sys.argv) > 2 else None
    receipts = []
    with tempfile.TemporaryDirectory(prefix='c6-globaltimer-') as folder:
        folder = Path(folder)

        def emit(tag, source, accepted):
            path = folder / (tag + '.ptx')
            path.write_text(source)
            result = subprocess.run([str(driver), str(path), '16', '32'],
                                    text=True, capture_output=True, timeout=5)
            receipts.append({'case': tag, 'exit_code': result.returncode,
                             'stderr': result.stderr.strip()})
            if accepted:
                assert result.returncode == 0, result.stderr
                return result.stdout
            assert result.returncode != 0 and not result.stdout, (tag, result.stdout)
            return None

        for clock in ('mov.u64', 'mov.b64'):
            output = emit(clock, program(clock), True)
            body = output[output.index('{', output.index('.entry kernel')):]
            # Locate real calls, excluding the preceding extern declarations.
            issue = re.search(r'call\s+\([^;]+__hbfsim_timing_future_issue_v1', body)
            waits = list(re.finditer(r'call\s+\([^;]+__hbfsim_timing_future_wait_v1', body))
            before = body.index(f'{clock} %t0, %globaltimer;')
            work = body.index('mad.lo.u32 %work, %work, 3, 5;')
            after = body.index(f'@%active {clock} %t1, %globaltimer;')
            consume = body.index('add.u32 %answer, %loaded, %work;')
            assert issue and waits and issue.start() < before < work < after < waits[0].start() < consume
            assert body.count('%globaltimer') == 2
            assert not any(wait.start() < after for wait in waits)
            if ptxas:
                path = folder / (clock + '.emitted.ptx')
                path.write_text(output)
                result = subprocess.run([str(ptxas), '-c', '-O3', '-arch=sm_120',
                                         str(path), '-o', str(folder / (clock + '.cubin'))],
                                        text=True, capture_output=True, timeout=15)
                assert result.returncode == 0, result.stderr
                receipts.append({'case': clock + '-assemble', 'exit_code': result.returncode})

        negatives = {
            'wrong-width': program(replacement='mov.u32 %work, %globaltimer;'),
            'write': program(replacement='mov.u64 %globaltimer, %addr;'),
            'arithmetic-source': program(replacement='add.u64 %t0, %globaltimer, 1;'),
            'shadow': program(declarations='.reg .b64 %globaltimer;'),
            'shadow-write': program(replacement='mov.u64 %globaltimer, %addr;', declarations='.reg .b64 %globaltimer;'),
            'unknown-half': program(replacement='mov.u32 %work, %globaltimer_lo;'),
        }
        for tag, source in negatives.items():
            emit(tag, source, False)
    print(json.dumps({'status': 'PASS', 'scope': 'CPU_ADMISSION_AND_EMISSION_ORDER',
                      'gpu_used': False, 'scientific_validation_passed': False,
                      'cases': receipts}, sort_keys=True))


if __name__ == '__main__':
    main()
