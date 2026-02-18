# Sample Data

Pre-built OpenADR 3 JSON payloads for manual testing or offline use. These can be sent directly to the VTN via `curl` or the `requests` library.

## Files

| File | Description |
|---|---|
| `program_pricing.json` | OpenADR 3 program definition for eTOU-Dyn dynamic pricing (PUC, USD/KWH) |
| `event_pricing.json` | OpenADR 3 event with 13 hourly price intervals sourced from the Olivine API |

## Usage

The `event_pricing.json` file contains a `PROGRAM_ID_PLACEHOLDER` value in the `programID` field. Replace it with the actual program ID returned when you create the program on the VTN.

These files are provided as a reference. The quickstart notebooks fetch live prices from the Olivine API and create the payloads programmatically.
