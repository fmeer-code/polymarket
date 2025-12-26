import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# Optional hover tooltips (recommended)
# pip install mplcursors
try:
    import mplcursors
except ImportError:
    mplcursors = None


def _coerce_api_time(value):
    """Return naive UTC datetime or None."""
    if value is None:
        return None
    ts = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.tz_convert("UTC").tz_localize(None)


def plot_lag_data(filename: str, api_1=None, api_2=None, api_3=None):
    try:
        # 1) Load data
        df = pd.read_csv(filename)

        # 2) Validate columns
        required = {"timestamp", "token", "price", "notional_usd", "trigger_ts"}
        missing = required - set(df.columns)
        if missing:
            raise KeyError(f"Missing columns: {sorted(missing)}")

        # 3) Parse timestamp and normalize types
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df["price"] = pd.to_numeric(df["price"], errors="coerce")
        df["notional_usd"] = pd.to_numeric(df["notional_usd"], errors="coerce")
        df["trigger_ts"] = pd.to_datetime(df["trigger_ts"], utc=True, errors="coerce")
        df = df.dropna(subset=["timestamp", "token", "price", "notional_usd"]).copy()

        # Optional: make timestamps naive local or keep UTC; here we keep UTC but remove tz for matplotlib
        df["time"] = df["timestamp"].dt.tz_convert("UTC").dt.tz_localize(None)
        first_trigger = df["trigger_ts"].dropna().min()
        trigger_time = (
            first_trigger.tz_convert("UTC").tz_localize(None) if pd.notnull(first_trigger) else None
        )

        # 4) Split by token and sort
        leader = df[df["token"].str.upper() == "LEADER"].sort_values("time")
        lagger = df[df["token"].str.upper() == "LAGGER"].sort_values("time")

        # 5) Plot
        fig, ax = plt.subplots(figsize=(12, 7))

        # Lines + visible points (so hover hits a dot)
        leader_line = ax.plot(
            leader["time"], leader["price"],
            label="Leader", color="blue", linewidth=2, alpha=0.85
        )[0]
        leader_scatter = ax.scatter(
            leader["time"], leader["price"],
            color="blue", s=30, alpha=0.9
        )

        lagger_line = ax.plot(
            lagger["time"], lagger["price"],
            label="Lagger", color="orange", linewidth=2, linestyle="--", alpha=0.85
        )[0]
        lagger_scatter = ax.scatter(
            lagger["time"], lagger["price"],
            color="orange", s=30, alpha=0.9
        )

        # 6) Styling
        ax.set_title(f"Leader vs Lagger Price (with Notional USD)\nFile: {filename}", fontsize=14)
        ax.set_ylabel("Price (USD)", fontsize=12)
        ax.set_xlabel("Time (UTC)", fontsize=12)
        ax.grid(True, which="major", linestyle="--", alpha=0.7)

        # Mark the first trigger timestamp
        if trigger_time is not None:
            ax.axvline(trigger_time, color="red", linestyle=":", linewidth=2, label="First trigger")

        # Optional manual API markers
        api_values = [api_1, api_2, api_3]
        api_colors = ["purple", "green", "black"]
        for idx, value in enumerate(api_values, start=1):
            api_time = _coerce_api_time(value)
            if api_time is not None:
                ax.axvline(
                    api_time,
                    color=api_colors[idx - 1],
                    linestyle="-.",
                    linewidth=1.8,
                    label=f"API {idx}",
                )

        ax.legend(fontsize=12)

        # Show milliseconds on the x-axis to match CSV precision
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S.%f"))
        fig.autofmt_xdate()

        # 7) Hover tooltips showing notional_usd (and price/time)
        if mplcursors is not None:
            # Build lookup arrays aligned with scatter points
            leader_rows = leader.reset_index(drop=True)
            lagger_rows = lagger.reset_index(drop=True)

            cursor = mplcursors.cursor([leader_scatter, lagger_scatter], hover=True)

            @cursor.connect("add")
            def _on_add(sel):
                artist = sel.artist
                idx = sel.index

                if artist is leader_scatter:
                    row = leader_rows.iloc[idx]
                    token = "LEADER"
                else:
                    row = lagger_rows.iloc[idx]
                    token = "LAGGER"

                t = row["time"]
                price = row["price"]
                notional = row["notional_usd"]

                sel.annotation.set_text(
                    f"{token}\n"
                    f"Time: {t.strftime('%H:%M:%S.%f')[:-3]}\n"
                    f"Price: {price:.4f}\n"
                    f"USD: {notional:.4f}"
                )
                sel.annotation.get_bbox_patch().set(alpha=0.9)

        else:
            print("Tip: install mplcursors for hover tooltips: pip install mplcursors")

        print(f"Displaying plot for {filename}...")
        plt.show()

    except FileNotFoundError:
        print(f"❌ Error: The file '{filename}' was not found.")
    except KeyError as e:
        print(f"❌ Error: CSV is missing required columns. ({e})")
    except Exception as e:
        print(f"An error occurred: {e}")


if __name__ == "__main__":
    # Run: python plotter.py  (or edit filename below)
    file_to_plot = "capture_1766658816.893386.csv"

    # Optional manual API timestamps (string/datetime). Leave as None to skip.
    api_1 = None  # e.g., "2025-01-24T12:34:56.123Z"
    api_2 = None
    api_3 = None

    plot_lag_data(file_to_plot, api_1=api_1, api_2=api_2, api_3=api_3)
