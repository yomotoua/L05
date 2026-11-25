from password import check_password


def test_short_password():
    assert check_password("abc") == False


def test_long_password():
    assert check_password("abcdefghijkl") == True
