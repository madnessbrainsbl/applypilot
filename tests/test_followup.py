import autopilot as A


def demo():
    t = A._followup_triggered
    assert t('If you stated "other" above please specify here', "Other")
    assert not t('If you selected "Other" or "Referral" above, please specify', "LinkedIn"), \
        "a follow-up whose condition isn't met stays empty (a cached 'No' went into it)"
    assert t('If you selected "Other" or "Referral" above, please specify', "Referral")
    assert t("(if you stated No) What is your current country of residence?", "No")
    assert not t("If yes, please explain how you know them", "No")
    assert not t("If yes, please explain", ""), "no earlier answer found -> don't guess"
    assert A._FOLLOWUP_Q.search("If yes, please explain") and not A._FOLLOWUP_Q.search("What is your current city")
    print("follow-up questions self-check OK")


def test_demo():
    demo()


if __name__ == "__main__":
    demo()
