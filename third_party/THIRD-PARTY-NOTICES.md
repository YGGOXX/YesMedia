# YesMedia 1.0.1 - Third-party components and licenses

This notice reflects dependencies declared by the current `main` source tree. Transitive dependency versions and the exact libraries packaged into an APK must be confirmed from Gradle/PIP resolution and APK inspection for each build.

## Android

| Component | Declared version / source | License | Use |
|---|---|---|---|
| Android Gradle Plugin | 8.9.2 (build-time) | Apache-2.0 | Android build |
| Kotlin Android plugin | 2.2.0 (build-time) | Apache-2.0 | Kotlin build |
| Chaquopy | 17.2.0 | MIT | Embeds Python 3.13 |
| ffmpeg-kit-maintained `ffmpeg-kit-full` | 8.1.9 | LGPL-3.0 variant | FFmpeg conversion and media processing |
| `requests` | installed by Chaquopy build | Apache-2.0 | HTTP client |
| `beautifulsoup4` | installed by Chaquopy build | MIT | HTML parsing |
| `yt-dlp` | installed by Chaquopy build | Unlicense | Site extractor integration |
| JUnit | 4.13.2 (test-only) | EPL-1.0 | JVM unit tests |
| JSON-java | 20240303 (test-only) | JSON License | JVM unit tests |

FFmpeg's LGPL-3.0 license text is included in [`LICENSE-ffmpeg.txt`](LICENSE-ffmpeg.txt); Chaquopy's MIT license text is included in [`LICENSE-chaquopy.txt`](LICENSE-chaquopy.txt). `ffmpeg-kit-full` is the non-GPL artifact; do not substitute a `-gpl` artifact without revisiting licensing and notices. The maintainers document `arm64-v8a` and `x86_64` builds; the APK still needs inspection to establish that these ABIs and all matching Chaquopy libraries were packaged.

## Project Python dependencies

The Android build installs the three runtime packages listed above. The desktop/development dependency set is defined in `requirements-desktop.txt`; the repository also contains Windows packaging tooling. Version ranges are not copied into this notice where they are not pinned in the runtime build declaration. Check resolved package metadata and include the required notices in any distributed desktop build.

## Links to upstream notices

- [Chaquopy](https://chaquo.com/chaquopy/doc/current/license.html)
- [ffmpeg-kit-maintained](https://github.com/ffmpegkit-maintained/ffmpeg)
- [Requests](https://github.com/psf/requests/blob/main/LICENSE)
- [Beautiful Soup](https://git.launchpad.net/beautifulsoup/tree/LICENSE)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp/blob/master/LICENSE)
- [JUnit 4](https://github.com/junit-team/junit4/blob/main/LICENSE-junit.txt)
- [JSON-java](https://github.com/stleary/JSON-java/blob/master/LICENSE)

## User responsibility

This software is intended for personal use with material the user is authorized to access and save. Users must comply with platform terms and applicable law.
