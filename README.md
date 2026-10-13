# Backyard Birds

A [Tesserae](https://github.com/dmellok/tesserae) widget showing the birds [BirdNET-Go](https://github.com/tphakala/birdnet-go) has heard today, illustrated with classic natural history plates by Audubon, Gould, Lear and others.

![Full bleed, name band](screenshots/lg.png)

Until you point it at a BirdNET-Go server, it runs a **demo garden**: a believable day of birdsong for wherever the cell's (or the app's) location is, in Australia, Europe or North America, built from the clock so it fills in through the morning. You can try every layout without a microphone.

## Connecting BirdNET-Go

Put BirdNET-Go's address in **BirdNET-Go address**, for example `http://birdnet.local:8080`. The widget reads two endpoints from BirdNET-Go's v2 API:

- `/api/v2/analytics/species/daily`: today's species, with counts per hour, first and last heard, and the new-species flag
- `/api/v2/detections/recent`: the latest individual detections

BirdNET-Go's reads stay public even with its Basic Authentication on (the password guards the settings pages), so **BirdNET-Go API token** is normally left blank. It is there for a reverse proxy or a BirdNET-Go build that wants a bearer token; the token is only ever sent to the BirdNET-Go address, never to a thumbnail host it links to.

Common names come from BirdNET-Go, so they appear in whatever language BirdNET-Go is set to.

The manifest declares `network:*` rather than a list of hosts, because the BirdNET-Go address is whatever you type in and Tesserae's capability guard matches hosts by name. What the widget actually reaches: your BirdNET-Go address, `commons.wikimedia.org` and `upload.wikimedia.org` for plates, `api.inaturalist.org` and `inaturalist-open-data.s3.amazonaws.com` for photos, and `fal.run` only when the AI picture options are on.

## Layouts

| Layout | What it shows |
| --- | --- |
| Field log | Today's birds as a list: picture, name, when last heard, calls today, with a 24-hour activity strip |
| Latest visitor | The bird just heard, large, beside today's tally |
| Gallery | A card per bird, with a "new today" flag |
| Day chart | Who called in which hour, with optional counts in the cells |
| Book plate | The latest bird as a page from a field guide, with an engraved-style caption |
| Cluster | Today's busiest birds on one page: a specimen sheet of their AI paintings, a blended AI scene, or a numbered montage of their plates and photos |
| Full bleed | The chosen bird's picture fills the cell, with one of fourteen caption styles: name band, editorial, field notebook, specimen tag, poster, folio plate, museum label, scientific figure, cyanotype, postage stamp, woodblock print, cameo, Art Nouveau, sonogram |
| Dawn chorus | Today's birds along a timeline at the time each was first heard, zoomed to the chorus itself; birds heard long before or after are noted |

The last four full-bleed styles are natural-history pieces. **Folio plate** is a double-ruled page from a Victorian folio, numbered by which bird of the day this was, with an engraved caption. **Museum label** sets the plate beside a collection card in printed field names and handwritten entries. **Scientific figure** draws the bird's calls per hour round a 24-hour dial with a figure caption. **Cyanotype** prints the picture as its own negative, white on Prussian blue, under the Latin name.

Four more frame the bird as an object. **Postage stamp** puts it on a perforated stamp, with the garden as its country and the day's calls as its value. **Woodblock print** gives it a tall title cartouche and a red seal, as on a Japanese kacho-e print. **Cameo** shows it in an oval window on a dark mat, like a Victorian cabinet card. **Art Nouveau** sets it under an arched frame with a title panel.

**Which bird** picks the bird the latest visitor, book plate and full bleed show: the one just heard, the rarest heard today (from the widget's own log of the days each bird was heard here), the one with most or fewest calls, or a new or returning species. **Draw the bird's song line** traces the bird's latest call under its name, from BirdNET-Go's sonogram of the detection; the **sonogram** caption shows the whole sonogram as a scientific strip. A bird heard again after six weeks or more away is marked *back* where new birds are marked *new*.

**After dark** uses sunrise and sunset for the location: *asleep* shows the moon's phase and the last bird heard once nothing has called for 90 minutes after dark, and *owls* keeps only the birds heard since sunset on the page.

**Bird names** can come from iNaturalist in another language, looked up once per bird and kept, with a region so that English names are the local ones.

Every layout adapts to portrait cells. At full bleed, a plate is never cropped: it is fitted whole into the space the caption leaves, and the rest of the cell is filled with the plate's own paper colour.

**Chart labels** puts numbers on the day chart's cells and the field log's activity strip: a count in every cell, or only in each bird's busiest hour.

## Panels

A Tesserae page is composed once and quantised for each panel afterwards, so the widget cannot tell what the panel can show; the **Panel** option says. **Colour** is the default. **16 greys** converts the pictures to greyscale with a gentle contrast stretch. **4 greys** stretches harder and snaps the day chart's heat scale to the panel's own four steps. **Black and white** lifts the mid-tones so a watercolour plate still reads once the panel dithers it, turns the heat scale into hatch patterns, frames the highlights instead of filling them, and thickens the fine serif and handwritten strokes.

![Day chart in black and white, with counts](screenshots/extra-11.png)

| | |
| --- | --- |
| ![Field log](screenshots/extra-1.png) | ![Latest visitor](screenshots/extra-2.png) |
| ![Gallery](screenshots/extra-3.png) | ![Day chart](screenshots/extra-4.png) |
| ![Book plate](screenshots/extra-5.png) | ![Editorial caption](screenshots/extra-6.png) |
| ![Field notebook caption](screenshots/extra-7.png) | ![Specimen tag caption](screenshots/extra-8.png) |
| ![Poster caption](screenshots/extra-9.png) | ![Portrait cells](screenshots/extra-10.png) |

## Pictures

- **Classic plates** come from `plates.json`, a reviewed pack of public-domain illustrations on Wikimedia Commons. Each entry records the file, a crop box around the bird and the source book. The widget fetches a plate once, crops and resizes it, and keeps it.
- **Photos** fill the gaps: BirdNET-Go's own species thumbnail, or in demo mode an openly licensed iNaturalist photo. Shown among plates, a photo is toned to a warm monotone so the set still matches; set **Photo colour** to lightly toned to keep most of a photo's colour under a warm cast, or to natural colour to leave photos as they are.
- **AI paintings (experimental)** repaint an openly licensed reference photo in the plate style through [fal.ai](https://fal.ai). Each bird is painted once and kept, at most one new painting per refresh and a daily limit set in the widget's settings (**AI paintings per day**: 6, 12 by default, 24, 48 or 96), at about US$0.04 a bird. A painting takes longer than Tesserae gives a widget to fetch its data, so it is made in the background: the bird shows a photo with a "Painting the …" note, and the painting appears on a later refresh. If fal.ai refuses (a wrong key, an empty balance), the reason is printed on the panel and that bird is not tried again for six hours. Paintings are credited as AI paintings on the panel. Add a fal.ai key in the widget's settings to use them.

**Art style** picks how AI paintings are made, from 47 styles in groups: natural history plates and prints (a hand-coloured lithograph after Gould, the default, a watercolour after Audubon, Japanese woodblock, black-ink engraving, linocut, field sketch, risograph, screen-print poster, mid-century modern after Harper), decorative styles round Art Nouveau (Art Nouveau illustration, Mucha poster, Jugendstil, Glasgow style, Vienna Secession, Art Deco, Arts and Crafts, Japonisme, Golden Age fairy-tale plate, Beardsley ink, Nouveau stained window), period and ornament (chinoiserie, Persian miniature, medieval bestiary, Byzantine icon, folk art, Delft tile, Edwardian cigarette card), fine art (ink and wash, pointillism, modern watercolour, charcoal), crafts (origami, paper cut, embroidery, needle felt, clay figure, brick-built, stained glass, mosaic), playful looks (anime, kawaii sticker, pixel art), a few jokes painted as real plates (Grumpy, tiny hats, superhero, Victorian portrait, pirate), and **Your own prompt**, a text box for the medium and look in your own words; the widget adds the bird and its reference photo, and each wording keeps its own paintings. Each style keeps its own painting of a bird, so switching back and forth costs nothing after the first time. Ink and wash, pixel art, risograph, Beardsley ink, stained glass and the engraving survive black-and-white panels best.

### The cluster layout

The cluster shows the day's busiest birds on one page. With classic plates on, the birds are cut from their plates, with no fal.ai needed; birds without a plate wait (or use a painting kept from before). With an AI picture option each bird is its own AI painting. Until any bird has a picture, the cluster is a numbered montage.

Three options shape the page. **Arrangement**: the look's own, an oval with the biggest in the middle, a circle, a ring round an empty middle, rows, scattered, or a heap with the biggest at the bottom. The birds are packed from the middle out and then pulled together until they touch, so the page reads as one tight group. **Bird size**: true to size from the bundled length table, rarest biggest, most calls biggest, or all the same. **Text under each bird**: any of name, scientific name, calls today, last heard, rarity in your garden, and a new-species mark, printed over the page rather than into the picture. Rarity comes from a log the widget keeps of the days each bird was heard; it settles after about a week and then reads first visit, rare here, occasional, regular or daily visitor.

Options that do not apply to the current setup are hidden in the editor (Tesserae 0.452 or later).

**Specimen sheet** (the default) is a page from a field guide: each bird's own AI painting is cut out and printed on one sheet, packed into an oval with the biggest bird in the middle, roughly true to size against each other (from a bundled table of body lengths for common garden birds), each named underneath. It shows up to 24 birds (**Cluster: how many birds**), costs nothing beyond the birds' own paintings, and fills in as they are painted, one per refresh. Building a page takes a few seconds, so it happens in the background and the previous page shows meanwhile.

Four more looks print the same paintings differently:

- **Numbered plate**: the birds numbered on the plate, with a key of names and today's counts beside it (below it in a tall cell).
- **Wreath**: the birds in a ring round a centred title and the date.
- **Field notebook**: the birds taped onto ruled paper at a slight tilt, with handwritten names.
- **Specimen drawer**: each bird in its own compartment on felt, in a wooden frame, with a museum tag.

**Arrangement: fal.ai composes the scene** shows up to six birds as one picture (this was the Blended scene style): once all of them are painted they are arranged in an oval and one more fal.ai pass repaints them together, with one branch, one background and one light. Two sub-options steer it: **Scene** (an oval vignette on branches, one shared branch, through one tree, round a bird bath, in a row on a wire, at a feeder, in a meadow, in flight, at a wetland, in a cottage garden) and **how free fal.ai is** (keep each bird as painted, or let it re-pose and move them). The picture is kept, one per scene and style, and composed again when the busiest birds change, at most as often as **Re-compose at most** allows (hourly to daily, three hours by default), at about US$0.04 a time.

AI paintings are asked for as a single bird and its perch alone on plain paper. The cut-out finds the paper by spreading in from the picture's edges, so a white bird outlined on white paper stays whole, and a painted grey ground or shadow under the bird is dropped with the paper. Progress notes (a painting under way, the day's limit reached) show on the paintings page, not on the panel.

### Managing the paintings

The widget has its own page under **Widgets → Backyard Birds** (`/plugins/backyard_birds/`) listing every painting it has made. From there you can:

- **Repaint** a bird. The current painting is kept as an earlier version, and any earlier version can be brought back or deleted.
- **Delete** a painting. With an AI picture option on, the bird is painted again the next time it is shown.
- **Use instead of the classic plate**, so a painting you like wins over the bird's plate, even with the plain "Classic plates, photos" option.
- **Paint a bird** now, by scientific name and in any art style, to compare a painting with its plate before choosing.
- See the cluster's blended pictures, one per art style, and delete one to have the widget blend the current birds again.
- Open any painting full size in a pop-up.
- Filter by art style, tick paintings (or select all shown) and delete them together, or delete every painting in a style at once. Bulk deletes move the paintings into earlier versions, so the birds are painted again as they are shown and nothing is lost.
- See today's count against the daily limit, reset it to carry on painting today, and see why a painting failed, with a button to let the widget try again.

A kept painting is also used for a bird without a plate whenever classic plates are on, so turning the AI option off does not throw the paintings away.

Credits for whatever is on screen are printed in the corner or footer.

### Growing the pack from the paintings page

The paintings page lists the birds this widget has heard that have no plate. **Find plates** looks for public-domain illustrations of the species on Wikimedia Commons in the background and shows the best few, cropped to the bird; **Use this** keeps one with the install (in `plates_extra.json`, never in the bundled pack), and it is used from the next refresh. Plates you accept win over AI paintings and photos; pull requests that add reviewed plates to the bundled pack are still welcome.

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

**Type on the printed looks** picks the typeface for the book plate, the full-bleed captions and the cluster pages: Classic (IM Fell English, the default), editorial italic (Playfair Display), typewriter (Special Elite), handwritten (Caveat), modern (Archivo) or the panel's own theme font. The field log, latest visitor, gallery and day chart always follow the panel's theme.

The caption styles bundle Playfair Display, Caveat, IM Fell English, Archivo Black and Archivo Narrow (SIL Open Font License) and Special Elite (Apache 2.0); their licences are in `fonts/`. Only the faces a style needs are sent with a render. On the black-and-white panel setting the fine serif and handwritten strokes are thickened so they survive dithering.

## Not yet

- Interface words ("just heard", "calls today") are English only; bird names follow BirdNET-Go.
- The demo garden covers Australia, Europe and North America; elsewhere it falls back to North America.

## Credit

The cluster's specimen sheet, the day's birds cut from old natural-history plates and laid out together on one page with the larger birds in the middle, follows [Fugleramme](https://github.com/arnegiacomo/fugleramme) by Arne Giacomo: a BirdNET-Go bird frame for a Raspberry Pi and an e-ink panel, with nearly 1,200 hand-cut public-domain illustrations. If you want a dedicated frame rather than a Tesserae widget, go and look at it. This widget is a separate implementation for Tesserae and shares no code or artwork with it.

## Licence

AGPL-3.0-or-later. Plates are public domain; photos and fonts keep their own licences, as credited.
