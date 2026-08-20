"""The wake matcher is the whole barge-in gate: too loose and Vega cuts itself off on
its own voice, too tight and it never yields the floor."""
from vega import WAKE, after_name


def test():
    for hit in ["vega", "Vega", "VEGA, what's the weather", "hey vega."]:
        assert WAKE.search(hit), hit
    for miss in ["vegan", "Las Vegas", "vegetable", "vegas baby"]:
        assert not WAKE.search(miss), miss

    # A barge turn arrives as echo, then the name, then you - keep the name and you.
    assert after_name("miles away sir Vega what's the weather") == "Vega what's the weather"
    assert after_name("vega stop") == "vega stop"
    # The name alone is not a question: empty means keep listening.
    assert after_name("Vega") == ""
    assert after_name("vega.") == ""
    # No name in the final transcript even though an interim carried it: trust the cut.
    assert after_name("what's the weather") == "what's the weather"


if __name__ == "__main__":
    test(); print("ok")
