from __future__ import annotations

import json
from pathlib import Path
import re
import unicodedata
from typing import Any, Collection

import pandas as pd


REQUIRED_COLUMNS = {
    "kode_bps_kabupaten_kota",
    "provinsi",
    "nama_kabupaten_kota",
    "nama_kecamatan",
    "tahun",
    "kepadatan_penduduk_per_km2",
    "sumber_bps_url",
}

SUBDISTRICT_ALIASES = {
    # Documented spelling differences between the BPS tables and the BIG
    # boundary layer. Each maps the BPS spelling onto the BIG one. The alias is
    # applied to both sides when normalising, so it only canonicalises a name;
    # a genuine same-spelling kecamatan elsewhere is unaffected, and any
    # accidental collision within one regency is caught by the duplicate check.
    # BPS: Blubur Limbangan; BIG: BL. Limbangan (Garut).
    "bluburlimbangan": "bllimbangan",
    # BPS: Cigemblong; BIG: Cigemlong (Lebak).
    "cigemblong": "cigemlong",
    # BPS: Grogol; BIG: Gerogol (Kota Cilegon).
    "grogol": "gerogol",
    # BPS: Purwareja Klampok; BIG: Purworeja Klampok (Banjarnegara).
    "purwarejaklampok": "purworejaklampok",
    # BPS: Kota Blora; BIG: Blora (Blora).
    "kotablora": "blora",
    # BPS: Pagerruyung; BIG: Pageruyung (Kendal).
    "pagerruyung": "pageruyung",
    # BPS: Petungkriono; BIG: Petungkriyono (Pekalongan).
    "petungkriono": "petungkriyono",
    # BPS: Ijen (formerly Sempol); BIG: Sempol (Bondowoso).
    "ijen": "sempol",
    # BPS: Pajarakan; BIG: Pejarakan (Probolinggo).
    "pajarakan": "pejarakan",
    # BPS: Gedek; BIG: Gedeg (Mojokerto).
    "gedek": "gedeg",
    # BPS: Giligenteng; BIG: Giliginting (Sumenep).
    "giligenteng": "giliginting",
    # BPS: Sumberrejo; BIG: Sumberejo (Bojonegoro).
    "sumberrejo": "sumberejo",
    # BPS: Canduang; BIG: Candung (Agam, Sumatera Barat).
    "canduang": "candung",
    # --- Sumatera Barat: Minang-dialect and numeral spellings ---
    # BPS: Koto Tujuh; BIG: Koto VII (Sijunjung).
    "kototujuh": "kotovii",
    # BPS: Lubuk Tarok; BIG: Lubuak Tarok (Sijunjung).
    "lubuktarok": "lubuaktarok",
    # BPS: 2 X 11 Anam Lingkuang; BIG: 2 X 11 Enam Lingkung (Padang Pariaman).
    "2x11anamlingkuang": "2x11enamlingkuang",
    # BPS: Anam Lingkuang; BIG: Enam Lingkung (Padang Pariaman).
    "anamlingkuang": "enamlingkung",
    # BPS: IV Koto Aua Malintang; BIG: IV Koto Aur Malintang (Padang Pariaman).
    "ivkotoauamalintang": "ivkotoaurmalintang",
    # BPS: Lubuak Aluang; BIG: Lubuk Alung (Padang Pariaman).
    "lubuakaluang": "lubukalung",
    # BPS: Sungai Garinggiang; BIG: Sungai Garingging (Padang Pariaman).
    "sungaigaringgiang": "sungaigaringging",
    # BPS: V Koto; BIG: V Koto Kampung Dalam (Padang Pariaman).
    "vkoto": "vkotokampungdalam",
    # BPS: VII Koto; BIG: VII Koto Sungai Sarik (Padang Pariaman).
    "viikoto": "viikotosungaisarik",
    # BPS: VII Koto Padang Sago; BIG: Padang Sago (Padang Pariaman).
    "viikotopadangsago": "padangsago",
    # BPS: VII Koto Patamuan; BIG: Patamuan (Padang Pariaman).
    "viikotopatamuan": "patamuan",
    # BPS: Dua Koto; BIG: Duo Koto (Pasaman).
    "duakoto": "duokoto",
    # BPS: Seberut Barat Daya; BIG: Siberut Barat Daya (Kepulauan Mentawai).
    "seberutbaratdaya": "siberutbaratdaya",
    # BPS: IX Koto; BIG: Sembilan Koto (Dharmasraya).
    "ixkoto": "sembilankoto",
    # BPS: Guguk Panjang; BIG: Guguak Panjang (Kota Bukittinggi).
    "gugukpanjang": "guguakpanjang",
    # --- Jambi ---
    # BPS: Depati VII; BIG: Depati Tujuh (Kerinci).
    "depativii": "depatitujuh",
    # BPS: Bathin II Babeko; BIG: Batin II Babeko (Bungo).
    "bathiniibabeko": "batiniibabeko",
    # BPS: Pasar Muara Bungo; BIG: Pasar Muaro Bungo (Bungo).
    "pasarmuarabungo": "pasarmuarobungo",
    # --- Sumatera Selatan ---
    # BPS: Kota Kayu Agung; BIG: Kayu Agung (Ogan Komering Ilir).
    "kotakayuagung": "kayuagung",
    # BPS: Semendo Darat Laut; BIG: Semende Darat Laut (Muara Enim).
    "semendodaratlaut": "semendedaratlaut",
    # BPS: Semendo Darat Tengah; BIG: Semende Darat Tengah (Muara Enim).
    "semendodarattengah": "semendedarattengah",
    # BPS: Semendo Darat Ulu; BIG: Semende Darat Ulu (Muara Enim).
    "semendodaratulu": "semendedaratulu",
    # BPS: Bulang Tengah Suku Ulu; BIG: BTS. Ulu (Musi Rawas).
    "bulangtengahsukuulu": "btsulu",
    # BPS: Suku Tengah Lakitan Ulu; BIG: STL Ulu Terawas (Musi Rawas).
    "sukutengahlakitanulu": "stluluterawas",
    # --- Bengkulu ---
    # BPS: Air Manjunto; BIG: Air Majunto (Muko Muko).
    "airmanjunto": "airmajunto",
    # BPS: Kaba Wetan; BIG: Kebawetan (Kepahiang).
    "kabawetan": "kebawetan",
    # --- Lampung ---
    # BPS: Way Pengubuan; BIG: Way Pangubuan (Lampung Tengah).
    "waypengubuan": "waypangubuan",
    # BPS: Braja Slebah; BIG: Braja Selebah (Lampung Timur).
    "brajaslebah": "brajaselebah",
    # BPS: Gedung Tataan; BIG: Gedong Tataan (Pesawaran).
    "gedungtataan": "gedongtataan",
}


class PopulationDensityDataError(ValueError):
    """Raised when population density data cannot be validated or joined."""


# Region-name equivalences applied after normalisation. BIG abbreviates the
# Kepulauan Seribu regency ("Adm. Kep. Seribu") while BPS writes it out.
REGION_ALIASES = {
    "kepseribu": "kepulauanseribu",
}


def normalize_region_name(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = text.encode("ascii", "ignore").decode("ascii").casefold()
    # BIG writes regencies without the "Kabupaten" prefix (for example
    # "Bandung"), while BPS writes "Kabupaten Bandung". Keep the "Kota"
    # marker so a regency and a city with the same stem remain distinct.
    # DKI's administrative-city label differs between the sources ("Kota Adm.
    # Jakarta Barat" vs "Kota Jakarta Barat"), so drop the "administrasi"/"adm"
    # token too.
    text = re.sub(r"\b(kabupaten|administrasi|adm)\b", "", text)
    key = re.sub(r"[^a-z0-9]+", "", text)
    return REGION_ALIASES.get(key, key)


def normalize_subdistrict_name(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = text.encode("ascii", "ignore").decode("ascii").casefold()
    key = re.sub(r"[^a-z0-9]+", "", text)
    return SUBDISTRICT_ALIASES.get(key, key)


def load_population_density_csv(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    try:
        # Keep CSV loading compatible with the Nuitka MinGW standalone build.
        density_df = pd.read_csv(path, encoding="utf-8-sig", engine="python")
    except (OSError, UnicodeError, pd.errors.ParserError) as exc:
        raise PopulationDensityDataError(
            f"Data kepadatan penduduk tidak dapat dibaca: {exc}"
        ) from exc

    missing = REQUIRED_COLUMNS - set(density_df.columns)
    if missing:
        raise PopulationDensityDataError(
            "Kolom data kepadatan penduduk tidak lengkap: "
            + ", ".join(sorted(missing))
        )

    density_df = density_df.copy()
    density_df["tahun"] = pd.to_numeric(density_df["tahun"], errors="coerce")
    density_df["kepadatan_penduduk_per_km2"] = pd.to_numeric(
        density_df["kepadatan_penduduk_per_km2"], errors="coerce"
    )
    if density_df[["tahun", "kepadatan_penduduk_per_km2"]].isna().any().any():
        raise PopulationDensityDataError(
            "Tahun atau nilai kepadatan penduduk mengandung data nonnumerik."
        )
    if (density_df["kepadatan_penduduk_per_km2"] < 0).any():
        raise PopulationDensityDataError("Nilai kepadatan penduduk tidak boleh negatif.")

    density_df["tahun"] = density_df["tahun"].astype(int)
    density_df["kepadatan_penduduk_per_km2"] = density_df[
        "kepadatan_penduduk_per_km2"
    ].astype(float)
    density_df["normalized_region_key"] = density_df["nama_kabupaten_kota"].map(
        normalize_region_name
    )
    density_df["normalized_subdistrict_key"] = density_df["nama_kecamatan"].map(
        normalize_subdistrict_name
    )
    if density_df.duplicated(
        ["normalized_region_key", "normalized_subdistrict_key"]
    ).any():
        raise PopulationDensityDataError(
            "Data kepadatan memiliki kecamatan ganda dalam kabupaten/kota yang sama."
        )
    return density_df


def load_subdistrict_geojson(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PopulationDensityDataError(
            f"Batas kecamatan tidak dapat dibaca: {exc}"
        ) from exc
    if payload.get("type") != "FeatureCollection" or not isinstance(
        payload.get("features"), list
    ):
        raise PopulationDensityDataError("Batas kecamatan bukan GeoJSON FeatureCollection.")
    return payload


def filter_population_density(
    density_df: pd.DataFrame,
    region_names: Collection[str] | None = None,
    density_range: tuple[int | float, int | float] | None = None,
    province_names: Collection[str] | None = None,
) -> pd.DataFrame:
    mask = pd.Series(True, index=density_df.index)
    if province_names:
        mask &= density_df["provinsi"].isin(province_names)
    if region_names:
        selected_region_keys = {
            normalize_region_name(name) for name in region_names
        }
        region_keys = density_df.get("normalized_region_key")
        if region_keys is None:
            region_keys = density_df["nama_kabupaten_kota"].map(
                normalize_region_name
            )
        mask &= region_keys.isin(selected_region_keys)
    if density_range is not None:
        low, high = density_range
        mask &= density_df["kepadatan_penduduk_per_km2"].between(low, high)
    return density_df.loc[mask].copy()


def filter_population_density_geojson(
    joined_geojson: dict[str, Any],
    region_names: Collection[str] | None = None,
    density_range: tuple[int | float, int | float] | None = None,
    province_names: Collection[str] | None = None,
) -> dict[str, Any]:
    """Filter an already-joined GeoJSON without copying its heavy geometries."""
    selected_region_keys = {
        normalize_region_name(name) for name in (region_names or [])
    }
    selected_provinces = set(province_names or [])
    low, high = density_range or (float("-inf"), float("inf"))
    return {
        "type": "FeatureCollection",
        "features": [
            feature
            for feature in joined_geojson.get("features", [])
            if (
                not selected_provinces
                or (feature.get("properties") or {}).get("provinsi")
                in selected_provinces
            )
            if (
                not selected_region_keys
                or normalize_region_name(
                    (feature.get("properties") or {}).get("kabupaten_kota", "")
                )
                in selected_region_keys
            )
            if low
            <= float(
                (feature.get("properties") or {}).get(
                    "kepadatan_penduduk_per_km2", -1
                )
            )
            <= high
        ],
    }


def join_population_density_geojson(
    subdistrict_geojson: dict[str, Any],
    density_df: pd.DataFrame,
    *,
    require_complete: bool = False,
) -> dict[str, Any]:
    lookup = {
        (row.normalized_region_key, row.normalized_subdistrict_key): row
        for row in density_df.itertuples(index=False)
    }
    matched_keys: set[tuple[str, str]] = set()
    joined_features: list[dict[str, Any]] = []

    for source_feature in subdistrict_geojson.get("features", []):
        source_properties = source_feature.get("properties") or {}
        key = (
            normalize_region_name(source_properties.get("WADMKK", "")),
            normalize_subdistrict_name(source_properties.get("WADMKC", "")),
        )
        row = lookup.get(key)
        if row is None:
            continue
        feature = {
            "type": "Feature",
            "properties": {
                "provinsi": row.provinsi,
                "kabupaten_kota": row.nama_kabupaten_kota,
                "kecamatan": row.nama_kecamatan,
                "tahun": int(row.tahun),
                "kepadatan_penduduk_per_km2": float(
                    row.kepadatan_penduduk_per_km2
                ),
            },
            # Geometry is never mutated, so retaining the cached reference avoids
            # a costly deep copy on every Streamlit rerun.
            "geometry": source_feature.get("geometry"),
        }
        joined_features.append(feature)
        matched_keys.add(key)

    if require_complete and len(matched_keys) != len(lookup):
        unmatched = sorted(set(lookup) - matched_keys)
        examples = ", ".join(f"{region}/{district}" for region, district in unmatched[:5])
        raise PopulationDensityDataError(
            f"{len(unmatched)} kecamatan tidak cocok dengan batas geospasial"
            + (f": {examples}" if examples else ".")
        )
    return {"type": "FeatureCollection", "features": joined_features}
