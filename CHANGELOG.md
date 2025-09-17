# Change Log

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](http://keepachangelog.com/)
and this project adheres to [Semantic Versioning](http://semver.org/).

## [0.0.3] - 2025-09-15

### Fixed
- Added maps to the preload command
- "Loose" fitting mode was not working as intended, this has been fixed.
- Adjustments to make the automatic fit detection check more accurate.

### Changed
- Doctrine Base Rewards adjusted to use Doctrine Fit ID instead of Ship ID, allowing multiple fits of the same ship to have different base rewards.
- "Admin Settings" page made useful. Restylized.

### Added
- Added the ability for admins to disable Discord notifications for users.
- Penalty Schemes now have an "Allow Substitutes" check box.
  - If a doctrine has refits in the cargo, the fit check will use the items in the cargo as "allowed substitutes". (If enabled on the penalty scheme)
- "Admin Settings" button added to the navbar
- Ignored Modules are now managed via the "Admin Settings" page.
  - Ignoring a module completely removes it from the fit check equations.
- Button to toggle strict or loose fit check on a submitted battle report.
- Click to copy to clipboard on Victim Name, Suggested and Actual payouts.

### Removed
- Removed the "Stats" button from the navbar
- Removed the dropdown containing "Manage Penally" and "Manage Doctrine" from the navbar

## [0.0.2] - 2025-09-13

### Fixed

- Styling of Penalty Scheme List to match other lists
- Users were unable to save Penalty Schemes
- Users received an error when attempting to save My Discord Settings
- Ships did not populate in the dropdown when creating a base Reward

### Attempted to fix
- Discord was not sending pings via aa-discordbot


## [0.0.1] - 2025-09-12

### Added

- Initial version
