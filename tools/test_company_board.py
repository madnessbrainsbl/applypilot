from ats_schema import _board_slugs, find_on_company_board


def demo():
    assert _board_slugs("IONOS Group") == ["ionos"]
    assert "sundaynatural" in _board_slugs("Sunday Natural") and "sunday-natural" in _board_slugs("Sunday Natural")
    assert _board_slugs("NTT DATA Europe & Latam")[0] == "nttdataand"

    board = (("Security Engineer", "u/1"), ("Senior Security Engineer (m/f/d)", "u/2"),
             ("Senior Cloud Security Engineer", "u/3"))
    assert find_on_company_board("x", "Senior Security Engineer (f/m/d)", board) == "u/2", \
        "gender tags differ between LinkedIn and the board"
    assert find_on_company_board("x", "Senior Security Engineer", board) == "u/2"
    assert find_on_company_board("x", "Staff Security Engineer", board) is None, \
        "a different seniority is a different posting"
    assert find_on_company_board("x", "Security Engineer", board) == "u/1"
    assert find_on_company_board("x", "", board) is None
    print("company board lookup self-check OK")


if __name__ == "__main__":
    demo()
