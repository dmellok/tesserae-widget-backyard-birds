# Backyard Birds

A [Tesserae](https://github.com/dmellok/tesserae) widget showing the birds [BirdNET-Go](https://github.com/tphakala/birdnet-go) has heard today, illustrated with classic natural history plates by Audubon, Gould, Lear and others.

![Full bleed, name band](screenshots/lg.png)

Until you point it at a BirdNET-Go server, it runs a **demo garden**: a believable day of birdsong for wherever the cell's (or the app's) location is, in Australia, Europe or North America, built from the clock so it fills in through the morning. You can try every layout without a microphone.

## Connecting BirdNET-Go

Put BirdNET-Go's address in **BirdNET-Go address**, for example `http://birdnet.local:8080`. The widget reads two endpoints from BirdNET-Go's v2 API:

- `/api/v2/analytics/species/daily`: today's species, with counts per hour, first and last heard, and the new-species flag
- `/api/v2/detections/recent`: the latest individual detections

Both are public unless BirdNET-Go's Private Mode is on. If it is, create an API token in BirdNET-Go and paste it into **BirdNET-Go API token**. The token is only ever sent to the BirdNET-Go address, never to a thumbnail host it links to.

Common names come from BirdNET-Go, so they appear in whatever language BirdNET-Go is set to.

## Layouts

| Layout | What it shows |
| --- | --- |
| Field log | Today's birds as a list: picture, name, when last heard, calls today, with a 24-hour activity strip |
| Latest visitor | The bird just heard, large, beside today's tally |
| Gallery | A card per bird, with a "new today" flag |
| Day chart | Who called in which hour. No pictures, so it suits black-and-white panels best |
| Book plate | The latest bird as a page from a field guide, with an engraved-style caption |
| Full bleed | The latest bird's picture fills the cell, with one of five caption styles: name band, editorial, field notebook, specimen tag, poster |

Every layout adapts to portrait cells. At full bleed, a plate is never cropped: it is fitted whole into the space the caption leaves, and the rest of the cell is filled with the plate's own paper colour.

| | |
| --- | --- |
| ![Field log](screenshots/extra-1.png) | ![Latest visitor](screenshots/extra-2.png) |
| ![Gallery](screenshots/extra-3.png) | ![Day chart](screenshots/extra-4.png) |
| ![Book plate](screenshots/extra-5.png) | ![Editorial caption](screenshots/extra-6.png) |
| ![Field notebook caption](screenshots/extra-7.png) | ![Specimen tag caption](screenshots/extra-8.png) |
| ![Poster caption](screenshots/extra-9.png) | ![Portrait cells](screenshots/extra-10.png) |

## Pictures

- **Classic plates** come from `plates.json`, a reviewed pack of public-domain illustrations on Wikimedia Commons. Each entry records the file, a crop box around the bird and the source book. The widget fetches a plate once, crops and resizes it, and keeps it.
- **Photos** fill the gaps: BirdNET-Go's own species thumbnail, or in demo mode an openly licensed iNaturalist photo. Shown among plates, a photo is toned to a warm monotone so the set still matches.
- **AI paintings (experimental)** repaint an openly licensed reference photo in the plate style through [fal.ai](https://fal.ai). Each bird is painted once and kept, at most one new painting per refresh and twelve a day, at about US$0.04 a bird. They are credited as AI paintings on the panel. Add a fal.ai key in the widget's settings to use them.

Credits for whatever is on screen are printed in the corner or footer.

### The plate pack

The first pack covers 29 common garden species across Australia, Europe and North America. Commons files illustrations as `Category:<scientific name> (illustrations)`, and a sample of common garden birds from seven regions (including Africa, South America and Japan) found illustrations for 50 of 56. So the pack can grow well beyond its first three regions, but each pick needs a person to check it: plates often show several species, and the best-scored file is not always the right bird.

`tools/build_plates.py` does the finding and cropping:

```sh
python tools/build_plates.py --species "Turdus merula" "Parus major"   # propose
open tools/out/review.html                                             # look
python tools/build_plates.py --accept "Turdus merula=1" "Parus major"  # take a pick
```

It keeps public-domain still images only, prefers known plate series (Audubon, Gould, Naumann, Lear, Keulemans, Thorburn, Siebold's *Fauna Japonica* and others), and crops each to the drawing, ignoring plate marks and captions. Pull requests that add reviewed plates are welcome.

## Fonts

The caption styles bundle Playfair Display, Caveat, IM Fell English, Archivo Black and Archivo Narrow (SIL Open Font License) and Special Elite (Apache 2.0); their licences are in `fonts/`. Only the faces a style needs are sent with a render. The fine strokes of the serif and handwritten styles suit colour and greyscale panels better than black-and-white ones.

## Not yet

- Interface words ("just heard", "calls today") are English only; bird names follow BirdNET-Go.
- The demo garden covers Australia, Europe and North America; elsewhere it falls back to North America.

## Licence

AGPL-3.0-or-later. Plates are public domain; photos and fonts keep their own licences, as credited.
