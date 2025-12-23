import sys
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

def plot_lag_data(filename):
    try:
        # 1. Load Data
        df = pd.read_csv(filename)

        # 2. Convert timestamp string to datetime objects
        df['time'] = pd.to_datetime(df['time'])

        # 3. Setup Plot
        fig, ax = plt.subplots(figsize=(12, 7))

        # 4. Plot Lines
        # Leader in solid blue
        ax.plot(df['time'], df['leader'], label='Leader (Trigger)', color='blue', linewidth=2, alpha=0.8)
        # Lagger in dashed orange
        ax.plot(df['time'], df['lagger'], label='Lagger (Follower)', color='orange', linewidth=2, linestyle='--')

        # 5. Styling
        ax.set_title(f"Event Analysis: Leader vs Lagger Price Action\nFile: {filename}", fontsize=14)
        ax.set_ylabel("Price (USD)", fontsize=12)
        ax.set_xlabel("Time (H:M:S)", fontsize=12)
        ax.grid(True, which='major', linestyle='--', alpha=0.7)
        ax.legend(fontsize=12)

        # Format X-axis to show HH:MM:SS nicely
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
        fig.autofmt_xdate() # Rotate dates to prevent overlap

        print(f"Displaying plot for {filename}...")
        plt.show()

    except FileNotFoundError:
        print(f"❌ Error: The file '{filename}' was not found.")
    except KeyError as e:
        print(f"❌ Error: CSV is missing required columns. (Missing: {e})")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    # Allows running via command line: python plotter.py my_data.csv

    file_to_plot = "lag_study_130946.csv"
    plot_lag_data(file_to_plot)
