import autopilot as A


def demo():
    form = "Apply for Security Engineer. Thank you for your interest in Acme! First name *"
    before = A._success_hits(form)
    assert not A._is_success(form, baseline=before), \
        "a phrase already on the form must not count as a submission"
    assert A._is_success(form + " Your application has been submitted.", baseline=before)
    assert not A._is_success("Autofill completed. Please review your details."), \
        "resume autofill is not a submission"
    assert A._is_success("", "https://jobs.example.com/thank-you")
    assert A._is_success("Thanks for applying!"), "no baseline keeps the old behavior"

    print("success detection self-check OK")


if __name__ == "__main__":
    demo()
