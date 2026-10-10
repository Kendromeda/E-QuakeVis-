from __future__ import annotations

import pytest

from utils.iscgem import ISCGEMError, in_region, load_iscgem

HEADER = (
    "# ISC-GEM Global Instrumental Earthquake Catalogue\n"
    "#         date          ,    lat   ,    lon   , smajax , sminax , strike, q ,  depth ,"
    "   unc , q ,  mw  ,  unc , q , s ,   mo  , fac,  mo_auth ,  mpp  ,  mpr  ,  mrr  ,"
    "  mrt  ,  mtp  ,  mtt  ,  str1  ,  dip1  , rake1  ,  str2  ,  dip2  , rake2  ,"
    "   type ,    eventid\n"
)
ROWS = (
    " 1938-02-01 19:04:18.07 ,   -5.053 ,  131.614 ,   25.1 ,   15.0 ,  97.6 , C ,   25.0 ,"
    "   8.4 , C , 8.36 , 0.20 , B , d ,  2.30 , 21 , bibliog  ,       ,       ,       ,"
    "       ,       ,       ,        ,        ,        ,        ,        ,        ,"
    "        ,  901234\n"
    " 2004-12-26 00:58:52.05 ,    3.316 ,   95.854 ,    4.1 ,    3.2 ,  44.5 , A ,   28.6 ,"
    "   1.7 , A , 9.31 , 0.10 , A , d ,  3.95 , 22 , gcmt     ,  1.04 , 10.40 ,  1.00 ,"
    "  2.98 , -0.89 ,  -1.04 ,    329 ,      8 ,    110 ,    128 ,     83 ,     87 ,"
    "    BDC ,  7453151\n"
    " 1950-01-01 00:00:00.00 ,   40.000 ,   20.000 ,    9.0 ,    9.0 ,  10.0 , C ,   15.0 ,"
    "  25.0 , C , 5.60 , 0.40 , C , p ,       ,    ,          ,       ,       ,       ,"
    "       ,       ,       ,        ,        ,        ,        ,        ,        ,"
    "        ,  111\n"
)


def test_load_iscgem_parses_padded_columns(tmp_path):
    path = tmp_path / "isc-gem-cat.csv"
    path.write_text(HEADER + ROWS, encoding="utf-8")
    frame = load_iscgem(path)
    assert len(frame) == 3
    aceh = frame.loc[frame["eventid"] == "7453151"].iloc[0]
    assert aceh["mw"] == pytest.approx(9.31)
    assert aceh["mw_src"] == "d" and aceh["mo_auth"] == "gcmt" and aceh["mw_q"] == "A"
    assert aceh["time"].year == 2004
    assert frame.loc[frame["eventid"] == "111", "mo_auth"].isna().all()

    region = {"minlatitude": -11.5, "maxlatitude": 6.5,
              "minlongitude": 94.5, "maxlongitude": 141.5}
    assert sorted(in_region(frame, region)["eventid"]) == ["7453151", "901234"]


def test_load_iscgem_rejects_other_formats(tmp_path):
    path = tmp_path / "other.csv"
    path.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    with pytest.raises(ISCGEMError):
        load_iscgem(path)
