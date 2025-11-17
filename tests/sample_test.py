import pytest
from alifTools.sample import parseFeatures


HHB_FEATURE_GLOBAL_END = 0xFFFFFFFF


@pytest.mark.parametrize(
    "text,features",
    [
        ("aalt", {"aalt": True}),
        ("+aalt,liga", {"aalt": True, "liga": True}),
        ("aalt,liga,+dlig", {"aalt": True, "liga": True, "dlig": True}),
        ("-aalt", {"aalt": False}),
        ("aalt=0,liga", {"aalt": False, "liga": True}),
        ("aalt=1,liga=0", {"aalt": True, "liga": False}),
        ("aalt,-liga,dlig", {"aalt": True, "liga": False, "dlig": True}),
        ("aalt[2:4]", {"aalt": [[2, 4, True]]}),
        ("aalt[2:4]=1", {"aalt": [[2, 4, True]]}),
        ("aalt[2:4]=0", {"aalt": [[2, 4, False]]}),
        ("-aalt[2:4]", {"aalt": [[2, 4, False]]}),
        ("aalt[2:]", {"aalt": [[2, HHB_FEATURE_GLOBAL_END, True]]}),
        ("-aalt[2:]", {"aalt": [[2, HHB_FEATURE_GLOBAL_END, False]]}),
        ("aalt[2]", {"aalt": [[2, 2, True]]}),
        ("-aalt[2]", {"aalt": [[2, 2, False]]}),
        ("aalt[2:4]=2", {"aalt": [[2, 4, 2]]}),
        ("aalt=3", {"aalt": 3}),
        ("aalt=3, aalt[5]=2", {"aalt": [[0, HHB_FEATURE_GLOBAL_END, 3], [5, 5, 2]]}),
    ],
)
def test_parse_feature(text, features):
    assert parseFeatures(text) == features
