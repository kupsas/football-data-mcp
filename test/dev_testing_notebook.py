import marimo

__generated_with = "0.19.11"
app = marimo.App(width="medium")


@app.cell
def _():
    import sys
    from rootutils import find_root

    sys.path.append(str(find_root() / "src"))
    import ScraperFC as sfc

    return (sfc,)


@app.cell
def _(sfc):
    # Capology was removed from the collect_data pipeline (Selenium-only site). Use other
    # scrapers here for ad-hoc experiments, e.g. sfc.Understat(), sfc.Transfermarkt(), …
    _ = sfc
    return


if __name__ == "__main__":
    app.run()
