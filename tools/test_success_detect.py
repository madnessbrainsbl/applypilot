import autopilot as A


def demo():
    form = "Apply for Security Engineer. Thank you for your interest in Acme! First name *"
    before = A._success_hits(form)
    assert not A._is_success(form, baseline=before), \
        "a phrase already on the form must not count as a submission"
    assert A._is_success(form + " Your application has been submitted.", baseline=before)
    assert not A._is_success("Autofill completed. Please review your details."), \
        "resume autofill is not a submission"
    assert A._is_success(
        "Success. Your application was successfully submitted. We'll contact you if there are next steps.")
    assert not A._is_success(
        "Autofill completed! Please review the information we filled in for you. "
        "Your form needs corrections. Missing entry for required field.")
    assert not A._is_success("", "https://jobs.example.com/thank-you"), "a URL alone is not acknowledgement"
    assert A._is_success("Thanks for applying!"), "no baseline keeps the old behavior"

    print("success detection self-check OK")

if __name__ == "__main__":
    demo()
