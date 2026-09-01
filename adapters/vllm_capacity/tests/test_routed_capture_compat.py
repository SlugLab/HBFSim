from routed_capture_compat import (
    install_vllm_routed_experts_deferred_binding,
)


class RecordingCapturer:
    def __init__(self) -> None:
        self.calls: list[tuple[int, object]] = []

    def capture(self, layer_id: int, topk_ids: object) -> None:
        self.calls.append((layer_id, topk_ids))


def make_fake_capturer_class():
    class FakeRoutedExpertsCapturer:
        instance = None

        @staticmethod
        def get_instance():
            return FakeRoutedExpertsCapturer.instance

    return FakeRoutedExpertsCapturer


def test_deferred_proxy_is_safe_before_init_and_forwards_after_init() -> None:
    capturer_class = make_fake_capturer_class()
    install_vllm_routed_experts_deferred_binding(capturer_class)

    bound_during_model_construction = capturer_class.get_instance()
    bound_during_model_construction.capture(0, "dummy")

    actual = RecordingCapturer()
    capturer_class.instance = actual
    bound_during_model_construction.capture(7, "real-topk")

    assert actual.calls == [(7, "real-topk")]
    assert capturer_class.get_instance() is actual


def test_install_is_idempotent_and_preserves_existing_instance() -> None:
    capturer_class = make_fake_capturer_class()
    actual = RecordingCapturer()
    capturer_class.instance = actual

    install_vllm_routed_experts_deferred_binding(capturer_class)
    first_getter = capturer_class.get_instance
    install_vllm_routed_experts_deferred_binding(capturer_class)

    assert capturer_class.get_instance is first_getter
    assert capturer_class.get_instance() is actual

