# Git LFS Setup Instructions

This repository uses Git LFS (Large File Storage) to handle large CSV data files.

## Installation

### macOS
```bash
brew install git-lfs
```

### Linux
```bash
# Ubuntu/Debian
curl -s https://packagecloud.io/install/repositories/github/git-lfs/script.deb.sh | sudo bash
sudo apt-get install git-lfs

# Or download from: https://git-lfs.github.com/
```

### Windows
Download and install from: https://git-lfs.github.com/

## Initial Setup (One-time per machine)

After installing Git LFS, run:
```bash
git lfs install
```

This only needs to be done once per machine.

## For Collaborators

1. Install Git LFS (see above)
2. Run `git lfs install` (one-time setup)
3. Clone the repository normally:
   ```bash
   git clone https://github.com/bassuaz24/kalshi_bets.git
   ```
4. Git LFS will automatically download the large files when you pull

## Adding New Large CSV Files

Large CSV files are automatically tracked with Git LFS. Just add them normally:
```bash
git add base/data_collection/kalshi_data/2026-01-15/markets_2026-01-15.csv
git commit -m "Add new market data"
git push
```

Git LFS will handle compression and storage automatically.

## Checking LFS Status

To see which files are tracked by LFS:
```bash
git lfs ls-files
```

## Storage Limits

- GitHub provides 1 GB of free LFS storage
- 1 GB of free bandwidth per month
- Additional storage/bandwidth can be purchased if needed

## Troubleshooting

If you get errors about LFS files:
1. Make sure Git LFS is installed: `git lfs version`
2. Make sure LFS is initialized: `git lfs install`
3. Try pulling LFS files explicitly: `git lfs pull`
