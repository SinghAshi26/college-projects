# Appnix Lead Finder

A streamlined lead generation, website auditing, and manual outreach platform designed for local businesses.

Built with **Python 3.12** and **Streamlit**.

Run with `streamlit run app.py` after installing `requirements.txt`.

If lead search fails, the app displays Google's error and a suggested fix. In the
Google Cloud project that owns your key, enable **Places API (New)**, link an
active billing account, and allow Places API (New) in the key's API restrictions.
This app sends requests from its Python server, so application restrictions must
allow the server's public IP; website referrer restrictions are not suitable.
See Google's [setup guide](https://developers.google.com/maps/documentation/places/web-service/get-api-key)
and [key restriction guidance](https://developers.google.com/maps/api-security-best-practices).

Set `GOOGLE_API_KEY` through Streamlit secrets, an environment variable, or the
sidebar. Restart the app after changing environment variables. A loaded key does
not confirm that Google has authorized access. Successful searches with no matches
show category/location guidance; failed searches show the error. If a later page
fails or the app's usage limit is reached, leads from completed pages are retained
with a warning.

Run search regression checks with `python -m unittest test_lead_search -v` (mocked
API calls; no Google credentials or billable requests required).
