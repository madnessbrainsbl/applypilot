import sponsors as S


def demo():
    S.set_register("uk", ["Monzo Bank Limited", "Darktrace Holdings PLC", "ACME Software Ltd T/A Rocket Labs",
                          "The Photo Studio (UK) Ltd"])
    S.set_register("nl", ["Adyen N.V.", "Booking.com B.V."])

    assert S.norm("Monzo Bank Limited") == "monzo bank"
    assert S.norm("Adyen N.V.") == "adyen" and S.norm("Booking.com B.V.") == "bookingcom"
    assert S.norm("Bühler GmbH") == "buhler"

    uk = lambda c: S.is_known_sponsor(c, "uk")
    assert uk("Monzo") and uk("monzo bank ltd") and uk("Darktrace") and uk("Darktrace plc")
    assert uk("Rocket Labs") and uk("ACME Software"), "trading-as names count too"
    assert uk("Photo Studio")
    assert not uk("Revolut") and not uk("Mon") and not uk("")
    assert S.is_known_sponsor("Adyen", "netherlands") and not S.is_known_sponsor("Adyen", "uk")

    offers = lambda j: "visa sponsorship" in (j.get("description") or "").lower()
    no_visa = lambda j: "worldwide" in (j.get("location") or "").lower()
    free = lambda j: "test exemption" if any(w in j.get("location", "") for w in ("Dubai", "Belgrade", "Yerevan", "Tbilisi", "Almaty", "Istanbul", "Budva")) else ""
    vp = lambda **j: S.visa_path(j, j.get("persona", "appsec"), offers, no_visa, free, cfg={"apply": {"sponsorship_waived": {"creative": "europe"}}})

    assert vp(company="Monzo", location="London, UK").startswith("UK")
    assert vp(company="Revolut", location="London, United Kingdom") is None, "UK, not on the register"
    assert S.licence_country({"location": "London, United Kingdom"}) == "uk"
    assert vp(company="Revolut", location="London", description="Visa sponsorship available").startswith("posting")
    assert vp(company="Adyen", location="Amsterdam").startswith("NL")
    assert vp(company="Mollie", location="Amsterdam, Netherlands") is None
    assert vp(company="Any", location="Berlin, Germany").startswith("Germany")
    assert vp(company="Any", location="Berlin, Germany", persona="creative").startswith("Europe creative"),         "creative in Europe: sponsorship waived by the user, not the Blue Card path"
    for loc in ("Dubai, UAE", "Belgrade, Serbia", "Yerevan", "Tbilisi, Georgia", "Almaty", "Istanbul, Türkiye", "Budva"):
        assert vp(company="Any", location=loc, persona="creative"), loc
    assert S.licence_country({"location": "London or Dubai"}, free) is None
    assert vp(company="Any", location="Atlanta, Georgia, USA") is None, "US state, not the country"
    assert vp(company="Monzo", location="Cambridge, MA") is None, "Cambridge MA is not the UK"
    assert vp(company="Any", location="Sydney, New South Wales") is None
    assert vp(company="Any", location="Remote - Worldwide") .startswith("remote")
    assert vp(company="Any", location="New York, NY") is None
    assert S.visa_path({"company": "Twilio", "location": "Remote - US", "title": "Global SOC Operator"},
                       "appsec", offers, lambda j: True, free) is None, "US-remote is not worldwide"
    assert S.licence_country({"location": "New York"}) is None

    assert S.name_score("Darktrace Cyber Ltd") == 2 and S.name_score("Acme Home Care Ltd") == 0
    assert S.name_score("Bob's Plumbing Ltd") == 0 and S.name_score("Blue Digital Ltd") == 1
    print("sponsors self-check OK")


def test_demo():
    demo()


if __name__ == "__main__":
    demo()


def test_europe_creative_waiver():
    import sponsors
    j = {"title": "Photographer", "company": "Zzz Studio", "description": ""}
    assert sponsors.visa_path({**j, "location": "Berlin, Germany"}, "creative", cfg={"apply": {"sponsorship_waived": {"creative": "europe"}}}), "Europe creative: waived by the user"
    assert sponsors.visa_path({**j, "location": "Remote - Europe"}, "creative", cfg={"apply": {"sponsorship_waived": {"creative": "europe"}}})
    assert not sponsors.visa_path({**j, "location": "London, ON"}, "creative"), "London Ontario is not Europe"
    assert not sponsors.visa_path({**j, "location": "Paris, TX"}, "creative")
    assert not sponsors.visa_path({**j, "location": "Madrid, Spain"}, "appsec"), "the waiver is for creative only"
