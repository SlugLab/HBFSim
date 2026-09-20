/* Syntax/input-structure probe for the pinned external 3D-ICE library.
 * No thermal_data_build, factorization, output generation, or emulation.
 */
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include "analysis.h"
#include "output.h"
#include "stack_description.h"
#include "stack_file_parser.h"

/* Stock parsing initializes pluggable heatsinks. Refuse that token before
 * entering its parser, including conservative matches in comments/paths.
 * Streaming matching also handles tokens split across input buffers. */
static int safe_input(const char *path) {
    FILE *input = fopen(path, "rb");
    if (!input) { perror(path); return 0; }
    const char token[] = "pluggable";
    size_t matched = 0;
    int ch;
    while ((ch = fgetc(input)) != EOF) {
        ch = tolower((unsigned char)ch);
        if (ch == token[matched]) ++matched;
        else matched = (ch == token[0]) ? 1 : 0;
        if (matched == sizeof(token) - 1) {
            fclose(input);
            fputs("REFUSED: pluggable heatsinks can execute code during parsing\n", stderr);
            return 0;
        }
    }
    const int ok = !ferror(input);
    fclose(input);
    return ok;
}

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "Usage: %s INPUT.stk\n", argv[0]);
        return 2;
    }
    if (!safe_input(argv[1])) return 2;
    StackDescription_t stack;
    Analysis_t analysis;
    Output_t output;
    stack_description_init(&stack);
    analysis_init(&analysis);
    output_init(&output);
    if (parse_stack_description_file(argv[1], &stack, &analysis, &output) != TDICE_SUCCESS) {
        /* Some stock error actions already destroy these structures. Avoid
         * double cleanup; the process exits and releases remaining memory. */
        fputs("{\"status\":\"PARSE_FAILED\",\"solve_performed\":false}\n", stdout);
        return 1;
    }
    printf("{\"status\":\"PARSE_ONLY_PASS\",\"solve_performed\":false,"
           "\"layers\":%llu,\"rows\":%llu,\"columns\":%llu,\"cells\":%llu}\n",
           (unsigned long long)stack.Dimensions->Grid.NLayers,
           (unsigned long long)stack.Dimensions->Grid.NRows,
           (unsigned long long)stack.Dimensions->Grid.NColumns,
           (unsigned long long)stack.Dimensions->Grid.NCells);
    output_destroy(&output);
    stack_description_destroy(&stack);
    analysis_destroy(&analysis);
    return 0;
}
