# PUDU manuals as Markdown (EN, DE, IT)

PUDU user manuals and accessory manuals, converted from the original multilingual PDFs to Markdown, one file per
language.

## Folder layout

```
manuals/                      robot manuals (13)
accessories/                  charging stations, docking stations, pager, … (9)
  <manual>/                   one folder per original PDF, named like it
    en.md  de.md  it.md       the manual's text in each language
    en.pdf de.pdf it.pdf      the pages of that language from the original PDF (content pages, no cover)
    images/                   the figures the Markdown files link to
    <manual>.pdf              the original multilingual PDF
```

The FlashBot, GW1 and Pager manuals have no Italian part, so they have no `it.md` / `it.pdf`.

## Inside a Markdown file

- **Header**: each file starts with a short YAML block: `title`, `language`, `source_pdf` (the original PDF),
  `reference_pdf` (the language PDF next to it) and `generated` (date).
- **Pages**: every printed page starts with a page line, e.g. `*Page 3 (PDF page 4, left)*`: printed page 3 is the
  left half of page 4 of the original PDF (many PDFs print two pages per sheet). Pages without a printed number
  show only the PDF page.
- **Headings** follow the manual's section numbering (`# 2. Product Components`, `## 2.3 Appearance`).
- **Figures** are linked from `images/` and stand where they appear on the printed page.
  - `> *Figure description (AI): …*` (DE: *Bildbeschreibung (KI)*, IT: *Descrizione della figura (IA)*) is a short
    description written by an AI model from the figure. It is not part of the PDF.
  - A `*Labels:*` list (DE: *Beschriftungen*, IT: *Etichette*) may follow a figure: the labels printed in it, read
    from the PDF. "**1** Lidar" is the entry for callout 1 of a numbered legend.
- **Tables** are Markdown tables with metric values only (the PDFs also print imperial ones). A cell that spans
  several rows or columns in the PDF is repeated in each row.
- **Lists** keep the printed numbering and nesting (bullets, numbered points, "Step 1 …").

## Manuals

| Folder | Manual |
|---|---|
| `manuals/BellaBot_Pro_User_Manual-multilanguage` | BellaBot Pro |
| `manuals/BellaBot_User_Manual-multilanguage` | BellaBot |
| `manuals/FlashBot_User_Manual_V1.0-multilanguage` | FlashBot (EN, DE) |
| `manuals/KettyBot_Pro_User_Manual_V3.0-multilanguage` | KettyBot Pro |
| `manuals/KettyBot_User_Manual_V1.0` | KettyBot |
| `manuals/PuduBot_2_User_Manual-multilanguage` | PuduBot 2 |
| `manuals/PUDU_CC1_Pro_User_Manual_multilanguage` | CC1 Pro |
| `manuals/PUDU_CC1_User_Manual-multilanguage` | CC1 |
| `manuals/PUDU_HolaBot_User_Manual-multilanguage` | HolaBot |
| `manuals/PUDU_MT1_User_Manual-multilanguage` | MT1 |
| `manuals/PUDU_SH1_User_Manual_V1.2-multilanguage` | SH1 |
| `manuals/PUDU_SwiftBot_User_Manual-multilanguage` | SwiftBot |
| `manuals/PUDU_T300_User_Manual-multilanguage` | T300 |
| `accessories/CC1_Charging_Station_User_Manual-multilanguage` | CC1 Charging Station |
| `accessories/CC1_Docking_Station_User_Manual-multilanguage` | CC1 Docking Station |
| `accessories/MT1_Charging_Station_User_Manual-multilanguage` | MT1 Charging Station |
| `accessories/PUDU_CC1_Seires_SelfCleaning_Docking_Station(Base)_User_Manual_multilanguage` | CC1 Series Self-Cleaning Docking Station (Base) |
| `accessories/PUDU_CC1_Seires_SelfCleaning_Docking_Station(Main)_User_Manual_multilanguage` | CC1 Series Self-Cleaning Docking Station (Main) |
| `accessories/PUDU_Charging_Station_Light_User_Manual-multilanguage` | Charging Station Light |
| `accessories/Pudu_GW1_User_Manual-multilanguage` | GW1 (EN, DE) |
| `accessories/PUDU_Mobile_Water_Station_User_Manual-multilanguage` | Mobile Water Station |
| `accessories/Pudu_Pager_User_Manual-multilanguage` | Pager (EN, DE) |
