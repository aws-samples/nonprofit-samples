# Harris County dataset notes

These notes document preparation of the four accompanying public-data extracts. They are
supporting documentation, not a separate measurement dataset or independent corroboration
of the extracts. The machine-readable provenance.yml preserves the source URLs and
transformation details. No live data is fetched during an analysis.

## NOAA observations
noaa-climate-summary-2024.json summarizes 366 daily NOAA NCEI observations from Houston
Intercontinental Airport, station USW00012960, for 2024. Annual and warm-season statistics
were derived from daily maximum temperature, minimum temperature, and precipitation.
One airport station is a regional proxy, not a neighborhood-level measurement. One year
does not establish a long-term climate trend.

## FEMA risk index
fema-national-risk-index.json transcribes the Harris County overall index and four selected
hazard rows from FEMA National Risk Index version 1.19.0, county FIPS 48201. Scores, ratings,
annualized frequencies, exposure, and expected annual loss retain their displayed values.
These are archived county-level model outputs, not parcel-level assessments.

## Environmental and demographic indicators
environmental-justice-blockgroups.json uses the Public Environmental Data Partners'
Environmental Justice Analysis Multisite service, which reconstructs archived EJScreen-style
indicators and is not an EPA service. Of 2,830 retrieved Harris County block groups, 2,733
valid records with population at least 500 were eligible for selection. The six illustrative
records include two low-demographic-burden comparisons, two highest-demographic-burden
records, and two combining high demographic and projected flood burden. They are not a
statistically representative county sample. Indicator definitions and vintages vary; the
access date of 2026-09-02 is not the observation date of every indicator.

## Landsat measurements
landsat-surface-temperature-samples.json contains clear-pixel statistics from five fixed
1.05 km by 1.05 km windows in a USGS Landsat 9 Collection 2 Level-2 surface-temperature
scene, LC09_L2SP_025039_20240908_20240909_02_T1_ST, acquired on 2024-09-08.
The raster was accessed through the Microsoft Planetary Computer mirror. QA_PIXEL fill,
dilated-cloud, cirrus, cloud, and cloud-shadow bits 0 through 4 were excluded. Digital
numbers were converted with scale 0.00341802 and offset 149.0 kelvin, then converted to
Celsius. Mean, median, and 90th-percentile clear-pixel temperatures were calculated.
Land-surface temperature is not air temperature. A single daytime scene cannot establish
seasonal or long-term heat exposure, and the sample windows are not administrative
neighborhood boundaries.
