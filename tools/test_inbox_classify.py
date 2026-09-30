import autopilot as A

def c(subject, body=""):
    return A.classify_email(subject, body)

# an automatic acknowledgement is not a reply from the company
assert c("Your application to Cloudflare", "Thank you for applying to Cloudflare.") == "received"
assert c("Application received", "We have received your application and will review it.") == "received"
assert c("Thank you for applying to OpenAI",
         "Thank you for applying. If your background fits, we will invite you to the next step in our process.") == "received", \
    "an acknowledgement describing the process is not an interview"
assert c("Security code for your application to Wolt", "Your code is 123456") is None

assert c("Update on your application",
         "Unfortunately we have decided to move forward with other candidates.") == "rejected"
assert c("Re: Security Engineer",
         "We regret to inform you that you have not been selected.") == "rejected"
assert c("Your application", "You are no longer under consideration for this role.") == "rejected"
assert c("Ihre Bewerbung", "Leider koennen wir Sie nicht beruecksichtigen.") == "rejected"

assert c("Interview invitation", "We would like to invite you to a technical interview.") == "interview"
assert c("Next steps", "Please book a time using https://calendly.com/acme/screen") == "interview"
assert c("Phone screen", "Are you available for a call next week?") == "interview"

assert c("Offer", "We are pleased to offer you the position of Security Engineer.") == "offer"

assert c("Thank you for interviewing with us",
         "Thank you for taking the time to interview with our team. "
         "Unfortunately we have decided to move forward with other candidates.") == "rejected", \
    "a post-interview rejection must not be misread as an interview invite"

assert c("Newsletter", "Check out our latest blog post about security.") is None
assert c("", "") is None

# only application mail is opened
assert A.is_job_mail("Update on your application to Ramp", "Ramp <no-reply@ashbyhq.com>")
assert A.is_job_mail("Anything", "no-reply@greenhouse.io")
assert not A.is_job_mail("Your weekly newsletter", "news@medium.com")

# the mail must come from the company applied to
job = {"company": "Flo Health", "url": "https://job-boards.greenhouse.io/flohealth/jobs/123"}
assert A._email_matches_job("Thanks for applying to Flo Health", "careers@example.com", job)
assert A._email_matches_job("Your application", "Talent <careers@flohealth.com>", job)
assert not A._email_matches_job("Your application", "no-reply@greenhouse.io", job), \
    "a shared ATS domain says nothing about which company wrote"
assert not A._email_matches_job("Your application to Acme Corp", "hr@acme.com", job)

eleven = {"id": "e", "company": "Elevenlabs", "title": "AI Creative Producer",
          "url": "https://jobs.ashbyhq.com/elevenlabs/1"}
ramp_mail = {"subject": "Update on your application to Ramp", "from": "Ramp <no-reply@ashbyhq.com>",
             "body": "... Powered by Ashby. Other companies hiring: ElevenLabs ..."}
assert A.match_mail_to_job(ramp_mail, [eleven]) is None, "Ramp's mail is not ElevenLabs' outcome"

assert A._company_tokens("Acme Technologies Inc") == ["acme"]
assert A._company_tokens("N26") == ["n26"]
assert A._company_tokens("The Software Group Ltd") == []

job_n26 = {"company": "N26", "url": "https://n26.com/en-eu/careers/positions/7597686"}
assert A._email_matches_job("Your application", "careers@n26.com", job_n26)
job_noname = {"company": "The Group Ltd", "url": "https://acme.com/careers/9"}
assert A._email_matches_job("Your application", "hr@acme.com", job_noname), \
    "a company whose name is all noise words still matches via its own domain"
assert not A._email_matches_job("Your application", "hr@unrelated.com", job_noname)

# several applications at one company: the posting title decides, never a guess
cf_video = {"id": "v", "company": "Cloudflare", "title": "Senior Video & Motion Producer"}
cf_red = {"id": "r", "company": "Cloudflare", "title": "AI Security Research & Red Team Engineer"}
mail = {"subject": "Cloudflare Application Update | AI Security Research & Red Team Engineer",
        "from": "Cloudflare <no-reply@greenhouse.io>", "body": "Unfortunately..."}
assert A.match_mail_to_job(mail, [cf_video, cf_red])["id"] == "r"
vague = {"subject": "Cloudflare Application Update", "from": "Cloudflare <no-reply@greenhouse.io>", "body": "Unfortunately..."}
assert A.match_mail_to_job(vague, [cf_video, cf_red]) is None, "which application is unknown -> not guessed"

print("inbox classifier self-check OK")
