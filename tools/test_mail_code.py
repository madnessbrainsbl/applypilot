import autopilot as A


def demo():
    assert A._extract_code("Your verification code is jkXrmzoK. It expires in 10 minutes.") == "jkXrmzoK"
    assert A._extract_code("Security code: 483920") == "483920"
    assert A._extract_code("Code\n\n  a1B2c3\n") == "a1B2c3"

    assert A._extract_code("Thanks for applying to Anthropic, we will be in touch shortly") is None, \
        "an ordinary sentence must not yield a fake code"
    assert A._extract_code("") is None
    assert A._extract_code(None) is None
    print("mail code self-check OK")


if __name__ == "__main__":
    demo()
