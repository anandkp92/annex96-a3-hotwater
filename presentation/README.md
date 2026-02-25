# Presentation

## Generating the PDF with Marp

Install the Marp CLI if you haven't already:

```bash
npm install -g @marp-team/marp-cli
```

Then, from this directory, run:

```bash
marp presentation.md --pdf -o annex96-a3-hpwh-oa3-usecase.pdf
```

This overwrites the existing PDF in place. To generate an HTML version instead:

```bash
marp presentation.md -o presentation.html
```
