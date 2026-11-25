from vowels import count_vowels


def test_banana():
    assert count_vowels("banana") == 3


def test_no_vowels():
    assert count_vowels("xyz") == 0
