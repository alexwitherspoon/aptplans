# FAA terms and systems

Unofficial glossary for AptPlans. Official sources remain the citation of record. This page is not FAA guidance and not legal advice.

How this project uses each source is in [Architecture](ARCHITECTURE.md). Airport identity is fetched on origin from NASR and NPIAS; the PDFs themselves are not in any federal catalog.

## At a glance

| Term | What it is | What AptPlans uses it for |
| --- | --- | --- |
| NASR APT | 28-day national landing-facility identity file | Page universe (public-use airports and seaplane bases) |
| NPIAS | Biennial national airport system plan | Likelihood flag (more likely to have a published plan) |
| Airport master plan | Sponsor planning study (recommended) | Catalog document `kind: master_plan` |
| ALP | FAA-approved layout drawing set (required if federally obligated) | Catalog document `kind: alp` |
| AIP grant histories | Annual Excel of issued grants by LocID (AIP, and AIG/COVID when included) | Airport Funding / Federal, and state Projects and allocations |
| AIG / IIJA | Formula BIL allocations and grant status by LocID | Issued AIG dollars appear in recent AIP workbooks |
| ADIP / 5010 / AMR | Facility data and internal ALP file store | Not a public master-plan library |
| SOAR | Internal NPIAS and AIP database | Not fetched |

There is no FAA dataset of master plan PDFs. Plans live on airport, city, county, consultant, and state sites.

## Documents we catalog

### Airport master plan

A planning study the FAA [strongly recommends](https://www.faa.gov/airports/resources/advisory_circulars/index.cfm/go/document.current/documentnumber/150_5070-6) (AC 150/5070-6B). It is not itself the grant-eligibility document. Scope varies by airport. Typical products are a narrative report plus an ALP update. Sponsors, consultants, and state aviation offices publish these; FAA does not keep a national PDF list.

### Airport Layout Plan (ALP)

The scaled drawing set of existing facilities and planned development. FAA approval means depicted existing and proposed development conforms to airport design standards (or an approved modification of standards). For federally obligated airports, a current ALP is a grant assurance (49 U.S.C. 47107(a)(16), Grant Assurance 29). Proposed development must appear on an FAA-approved ALP to be eligible for AIP funding. An ALP without a narrative master plan is still a first-class catalog record (`kind: alp`), not `no_plan_known`.

### AC 150/5070-6B

FAA advisory circular *Airport Master Plans*. Method guidance for writing a study. AptPlans uses it as the shape card for classifying a fetched PDF as a plan versus a newsletter or other file. It is not a list of completed plans.

### AC 150/5070-7

FAA advisory circular *The Airport System Planning Process*. State and regional system plans (SASP), not an individual airport master plan. State SASP URLs belong on state hub pages; they are not airport master plans.

### AC 150/5300-13

FAA advisory circular *Airport Design*. Design standards the ALP is checked against. Not a document we ingest.

## Airport identity

### NASR

National Airspace System Resource. FAA 28-day subscription of aeronautical data. [Listing](https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/). AptPlans fetches the current **APT CSV** zip (for example `{DD}_{Mon}_{YYYY}_APT_CSV.zip` from `nfdc.faa.gov`) and reads `APT_BASE.csv` for LID, name, ICAO, city, county, coordinates, elevation, ownership, public-use status, fuel types, and transient hangar/tiedown flags. `APT_RWY.csv` supplies runway ident, length, width, and surface type for the unofficial airport fact sheet when listed plans do not already give those figures.

NASR is the **superset of airports we would consider**. Default pages are public-use airports and seaplane bases (`FACILITY_USE_CODE` PU, `SITE_TYPE_CODE` A or C). Private-use strips and heliports are not paged unless a plan or GitHub issue admits them.

### NFDC

National Flight Data Center. Hosts NASR extra files such as the APT CSV zip. Same 28-day cycle as the NASR listing.

### NPIAS

National Plan of Integrated Airport Systems. Biennial FAA report of airports that are part of the national system and generally eligible for AIP. [Program page](https://www.faa.gov/airports/planning_capacity/npias). AptPlans parses **Appendix A** (current workbook is the 2025-2029 edition) for LID, name, city, state, ownership, service level, hub, and role.

NPIAS is a **likelihood overlay**, not a gate. Those airports are more likely to publish a master plan or ALP. An airport not in NPIAS can still have a plan. A well-formed FAA LID plus official URL is enough to queue intake.

NPIAS development estimates come from local master plans, ALPs, CIPs, and inspections. The report does not attach those files.

### Hub and role

NPIAS labels for how an airport sits in the system (large / medium / small hub, nonhub, reliever, national, regional, local, general aviation, and related service levels). Shown on airport pages when known. Absence of a role means not in the current Appendix A, not that the airport is out of scope.

### Public-use and private-use

NASR `FACILITY_USE_CODE`: PU (open to the public) versus PR (private). Master plans and ALPs are typically public-airport planning documents. AptPlans pages PU airports and seaplane bases from NASR; PR facilities can still be admitted from a found plan or issue.

### LID

FAA location identifier (LocID), three or four characters (PDX, 4S9). Primary airport key in this catalog. An ICAO code like KPDX is normalized to the LID when it appears on an intake issue.

### ICAO and IATA

ICAO is the four-character international code (KPDX). IATA is the three-character airline code when one exists. NASR supplies ICAO when present. OurAirports may supply IATA and an official home page (`home_link`, http(s) only). Airport pages show the FAA LID only; ICAO is the fallback when no LID exists. Neither replaces LID as the catalog key. A failed OurAirports fetch does not block the NASR/NPIAS overlay.

### OurAirports

Public-domain airport list used on origin to fill blank IATA codes and official home pages. AptPlans matches US rows by FAA LID (`local_code`, or ICAO `Kxxx` when that is missing). Closed and heliport rows lose to an open airport with the same LID. CI does not live-fetch this file.

### Airport Master Record (5010, AMR)

FAA Form 5010 facility data (runways, ownership, manager, based aircraft, and so on), served from ADIP. The name is easy to confuse with an **airport master plan**. It is operational identity, not a planning study. AptPlans does not treat a 5010 PDF as a master plan or ALP. Overview runway dimensions come from the NASR APT zip already fetched for identity, not from a 5010 download. Based aircraft and operations stay on listed plans until those figures appear in a catalog source we already keep.

## FAA systems (usually not public PDFs)

### ADIP

[Airport Data and Information Portal](https://adip.faa.gov/). Authoritative portal for 5010/AMR data, Airports GIS, and an internal **document library**. After ALP approval in OE/AAA, FAA offices are expected to upload ALP drawing sets into ADIP (see EPM 23-02). That library is for agency use and collaboration. It is not a public bulk dump of master plans.

### Airports GIS, AGIS, eALP

Geospatial airport data and electronic ALP submissions in support of NextGen and procedure design. Digital ALP geometry, not the narrative master plan PDF.

### OE/AAA

Obstruction Evaluation / Airport and Airspace Analysis. Where FAA reviews airspace and, for obligated airports, uses the ALP in lieu of some separate notices. Approval workflow for ALP changes, not a document catalog.

### SOAR

System of Airports Reporting. Internal FAA database that joins NPIAS planning, ACIP, and AIP funding. Behind the published NPIAS figures. Not fetched by this project.

### SOP 2.00

FAA Standard Operating Procedure for review and approval of ALPs. Process inside the Office of Airports. Checklists and approval-letter templates, not a list of plans.

### ADO

Airports District Office. Field office that reviews ALPs and AIP grants for a region. Sponsors file with the ADO; the public still gets the PDF from the sponsor.

## Funding and obligation

### AIP

Airport Improvement Program. Federal grants for eligible airport development and planning.

The public **dataset of issued grants** is the annual All Grants workbook on [AIP grant histories](https://www.faa.gov/airports/aip/grant_histories) (Excel from about FY 2005 onward; PDFs go back further). Rows are keyed by **LocID** plus fiscal year, grant sequence, entitlement versus discretionary amounts, and a brief project description. Descriptions often include `Update Airport Master Plan Study` or `Conduct Airport Master Plan Study`. That is evidence a study was funded, not the PDF. FAA also publishes a Tableau [Grant History Visualization (FY 2005-2025)](https://www.faa.gov/airports/aip/grant_histories) covering AIP, COVID relief, and IIJA by airport. That dashboard is interactive, not a bulk API.

These workbooks are fetched on origin into overlay `grants.jsonl` (monthly, or if the file is missing or empty) and listed on each airport page and on the state Projects and allocations list. The FAA amount is the announced award. Each grant number links to the USAspending award (`ASST_NON_{FAIN}_069`, hyphens stripped from the FAA grant sequence) and to that fiscal year's AIP summary page. CI must not live-fetch them. A grant row must not be treated as a complete master plan or ALP.

### AIG (IIJA / BIL)

Airport Infrastructure Grants under the Infrastructure Investment and Jobs Act. Formula allocations by LocID ([funding amounts](https://www.faa.gov/iija/iija-airport-infrastructure-grant-funding-amounts)) plus Grant Status Lists of what has actually been awarded. Separate from annual AIP, and time-limited (FY 2022-2026). Competitive **Airport Terminal Program** awards are announced as lists, not the same formula file.

### USAspending

[USAspending.gov](https://www.usaspending.gov/) has Assistance Listing **20.106** (legacy AIP, IIJA airport programs, and COVID airport grants through FY 2025) and **20.116** (AIP from FY 2026). Origin refresh POSTs FAA grant numbers (hyphens stripped) as `award_ids` to the `spending_by_award` search, 50 at a time with a pause between batches. **Award Amount** is obligated; **Total Outlays** is spent; still obligated is obligated minus outlays (or the FAA amount minus outlays if Award Amount is missing). Those figures appear on each grant and on the airport totals. Recipients are usually a city or airport authority, so LocID join stays on the FAA workbooks. If USAspending errors, keep the FAA announced amounts. CI must not live-call the API.

### Federally obligated airport

An airport that has accepted AIP (or certain other federal) grants and remains bound by grant assurances, including keeping a current ALP. Most NPIAS airports are in this set. Obligation is not the same as "has a published narrative master plan."

### CIP / ACIP

Capital Improvement Plan (sponsor) and Airport Capital Improvement Program (FAA programming). Project lists that feed NPIAS and AIP. Not ingested as master plans. Sponsor CIP rows are not in the FAA All Grants workbook until they become issued awards.

## Related studies (not ingested yet)

Named here so they are not confused with master plans or ALPs. Architecture allows naming them on an airport page later.

| Term | Meaning |
| --- | --- |
| SASP | State Aviation System Plan (AC 150/5070-7) |
| Part 150 | FAA airport noise compatibility planning |
| NEPA | Environmental review for federal actions; often a separate PDF from the master plan |
| Minimum standards | Sponsor rules for commercial operators on the airport |
| SSI | Sensitive Security Information; filenames and drawings that look like SSI are not stored |

## Official links

| Resource | URL |
| --- | --- |
| AC 150/5070-6B Airport Master Plans | https://www.faa.gov/airports/resources/advisory_circulars/index.cfm/go/document.current/documentnumber/150_5070-6 |
| NPIAS | https://www.faa.gov/airports/planning_capacity/npias |
| NASR subscription | https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/ |
| ADIP | https://adip.faa.gov/ |
| AIP grant histories (Excel by fiscal year) | https://www.faa.gov/airports/aip/grant_histories |
| FY 2025 AIP All Grants workbook | https://www.faa.gov/sites/faa.gov/files/2025-11/FY_2025_AIP_Grants.xlsx |
| IIJA AIG allocations and status | https://www.faa.gov/iija/iija-airport-infrastructure-grant-funding-amounts |
| USAspending (ALN 20.106 / 20.116) | https://www.usaspending.gov/ |
| Airports GIS / eALP | https://www.faa.gov/airports/planning_capacity/airports_gis_electronic_alp |
| ALP SOP and related SOPs | https://www.faa.gov/airports/resources/sops |
| ADIP ALP upload memo (EPM 23-02) | https://www.faa.gov/sites/faa.gov/files/EPM_23_02_ADIP_Document_Library.pdf |
