import autopilot as A


def demo():
    job = {"applied_at": "2026-09-28T10:00:00+00:00", "company": "Acme", "title": "Security Engineer"}

    def mail(subject, body, sender="Acme <no-reply@ashbyhq.com>", date="2026-09-28T20:00:00+00:00"):
        return {"subject": subject, "body": body, "from": sender, "date": date}

    quick = A.analyze_outcome_mail(mail("Update on your application", "We have decided to move forward with other candidates whose "
                                        "experience more closely matches our needs."), job, "rejected")
    assert quick["kind"] == "auto" and quick["hours"] == 10.0, quick

    visa = A.analyze_outcome_mail(mail("Your application", "Unfortunately we are unable to offer visa sponsorship for this role.",
                                       date="2026-10-03T09:00:00+00:00"), job, "rejected")
    assert visa["reason"] == "виза / право на работу", visa

    after_call = A.analyze_outcome_mail(mail("Next steps", "Thank you for taking the time to interview with our team. We will not be "
                                             "moving forward.", sender="Jane Smith <jane@acme.com>", date="2026-10-10T09:00:00+00:00"),
                                        job, "rejected")
    assert after_call["kind"] == "manual" and after_call["reason"] == "после интервью", after_call

    assert A.classify_email("Acme coding challenge", "Please complete the following assessment: https://www.hackerrank.com/test/abc") == "assessment"
    test_mail = A.analyze_outcome_mail(mail("Acme coding challenge", "Please complete it here: https://www.hackerrank.com/test/abc123 ."),
                                       job, "assessment")
    assert test_mail["link"].startswith("https://www.hackerrank.com/test/abc123"), test_mail
    assert A.classify_email("Thank you for applying to Acme",
                            "Thank you for applying. If selected, we will move you forward to the next stage.") == "received", \
        "an acknowledgement that mentions the next stage is not an invitation"
    assert A.classify_email("Update", "We regret to inform you we are not moving forward to the next stage.") == "rejected"
    assert A.classify_email("Your Okta Application - Staff Backend Engineer",
                            "We appreciate your interest in the Staff Backend Engineer role at Okta. We noticed that you've "
                            "recently applied to other roles with us as well. A member of our recruiting team will be in "
                            "touch if your background aligns with any of the positions. We also invite you to join our Talent "
                            "Community (https://talent.okta.com/) to stay up to date.") in ("received", None), \
        "Okta's acknowledgement was reported as 8 interviews"
    assert A.classify_email("Butternut Box Application",
                            "Thank you very much for applying. Unfortunately, we will not be able to offer you an interview "
                            "on this occasion. We have had to be really specific about the applicants we progress to the "
                            "next stage. We'd encourage you to follow us on Linkedin.") == "rejected", \
        "Butternut Box's rejection was reported as a next stage"
    assert A.classify_email("Canonical written Interview reminder - Engineering Manager",
                            "This is an automated reminder because the written interview submission for this application "
                            "to Canonical is due in 7 days.") == "assessment", "a written-interview deadline is a real task"
    assert A.classify_email("Interview for Canonical role - Senior Security Operations Engineer",
                            "Thank you for your interest in Senior Security Operations Engineer at Canonical. I have reviewed "
                            "your resume and moved you to this written interview stage.") == "assessment"
    assert A.classify_email("Thank you for applying to Canonical",
                            "Thank you for applying. For reference, our interview process starts with a written interview, "
                            "which will be reviewed in an anonymized queue.") == "received"
    print("outcome report self-check OK")


def test_demo():
    demo()


if __name__ == "__main__":
    demo()
