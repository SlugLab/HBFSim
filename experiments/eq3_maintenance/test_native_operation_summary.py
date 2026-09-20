import unittest

from experiments.eq3_maintenance.native_operation_summary import summarize_rows


def row(command, phase, time, transaction, operation, source, channel=0, die=0, plane=0):
    return {
        "command_id": str(command), "phase": str(phase), "time_ns": str(time),
        "transaction_id": str(transaction), "type": str(operation),
        "source": str(source), "channel": str(channel), "chip": "0",
        "die": str(die), "plane": str(plane),
    }


class NativeOperationSummaryTest(unittest.TestCase):
    def test_deduplicates_command_children_and_keeps_actual_program_erase_separate(self):
        rows = [
            # One multi-plane READ command: two flattened child rows per phase.
            row(10, 1, 100, 1, 0, 0, channel=0, plane=0),
            row(10, 1, 100, 2, 0, 0, channel=0, plane=1),
            row(10, 2, 130, 1, 0, 0, channel=0, plane=0),
            row(10, 2, 130, 2, 0, 0, channel=0, plane=1),
            # DATA_OUT is a child-transaction event: same command and phase,
            # distinct child timestamps must not be collapsed or rejected.
            row(10, 3, 131, 1, 0, 0, channel=0, plane=0),
            row(10, 3, 132, 2, 0, 0, channel=0, plane=1),
            row(10, 4, 141, 1, 0, 0, channel=0, plane=0),
            row(10, 4, 142, 2, 0, 0, channel=0, plane=1),
            # Actual maintenance PROGRAM and GC erase are distinct commands.
            row(20, 1, 200, 3, 1, 4, channel=1, die=2, plane=3),
            row(20, 2, 260, 3, 1, 4, channel=1, die=2, plane=3),
            row(30, 1, 300, 4, 2, 2, channel=1, die=2, plane=3),
            row(30, 2, 390, 4, 2, 2, channel=1, die=2, plane=3),
        ]
        result = summarize_rows(rows, source_name="fixture")

        self.assertEqual(result["deduplication"]["input_rows"], 12)
        self.assertEqual(result["deduplication"]["native_phase_events"], 10)
        self.assertEqual(result["deduplication"]["child_transactions"], 4)
        self.assertEqual(result["operations"]["READ"]["media_command_starts"], 1)
        self.assertEqual(result["operations"]["READ"]["child_transactions"], 2)
        self.assertEqual(result["operations"]["PROGRAM"]["paired_media_duration_ns"]["sum"], 60)
        self.assertEqual(result["operations"]["ERASE"]["media_command_starts"], 1)
        self.assertEqual(result["operations"]["PROGRAM"]["source_counts"], {
            "HBF_MAINTENANCE": {"media_command_starts": 1, "media_command_ends": 1},
        })
        # PROGRAM and ERASE deliberately share one physical tuple; tuple
        # coverage is deduplicated independently of operation count.
        self.assertEqual(result["actual_channel_chip_die_plane_tuple_count"], 3)
        self.assertEqual(result["actual_channel_die_plane_tuple_count"], 3)
        self.assertIn("NVM_Transaction.h", result["enum_contract"]["verified_source"]["operation_and_source"])
        self.assertEqual(result["completion_semantics"]["media_end"],
                         "NAND_MEDIA_PHASE_ENDED_NOT_COMMITTED_SUCCESS")
        self.assertEqual(result["unavailable"]["program_erase_lifetime_cycles"],
                         "UNAVAILABLE_NOT_OBSERVED")
        self.assertEqual(result["unavailable"]["payload_or_byte_integrity"],
                         "UNAVAILABLE_NOT_OBSERVED_METADATA_VALIDITY_ONLY")

    def test_rejects_identity_change_and_media_time_reversal(self):
        changed = [row(1, 1, 10, 1, 0, 0), row(1, 2, 20, 1, 1, 0)]
        with self.assertRaisesRegex(ValueError, "changed identity"):
            summarize_rows(changed)
        reversed_time = [row(1, 1, 20, 1, 0, 0), row(1, 2, 10, 1, 0, 0)]
        with self.assertRaisesRegex(ValueError, "precedes"):
            summarize_rows(reversed_time)


if __name__ == "__main__":
    unittest.main()
