from email_check import is_valid_email


def test_valid_email():
    assert is_valid_email("ana@upb.ro") == True


def test_empty_email():
    assert is_valid_email("") == False
