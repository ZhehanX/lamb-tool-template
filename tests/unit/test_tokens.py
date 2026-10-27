from app.workshop.tokens import TokenCounter, raw_o200k_count


def test_digits_count_one_token_each():
    plain = TokenCounter(factor=1.0, split_digits=False)
    split = TokenCounter(factor=1.0, split_digits=True)
    assert plain.count("123456789") == raw_o200k_count("123456789") == 3
    assert split.count("123456789") == 9
    assert split.count("Any 2024") == raw_o200k_count("Any ") + 4


def test_factor_scales_text_but_not_digits():
    t = TokenCounter(factor=2.0, split_digits=True)
    assert t.count("hola") == 2 * raw_o200k_count("hola")
    assert t.count("12") == 2


def test_message_overheads():
    t = TokenCounter(factor=1.0, message_overhead=5, prompt_overhead=16)
    msgs = [{"role": "user", "content": ""}, {"role": "assistant", "content": ""}]
    assert t.count_messages(msgs) == 16 + 2 * 5


def test_name_says_it_is_an_estimate_basis():
    assert TokenCounter().name == "o200k_base x 1.15, 1 token per digit"
