# hlx-testengine
hlx-testengine

# Helix -> testengine
TestRunId
Recording
TestName



# testengine -> Helix
              TestName: testName,
              FullTitle: fullTitle,
              Status: status,
              DurationMs: duration,
              RunTime: runTime,
              FileName: file,
              SuiteTitle: suiteTitle,
              ErrorMessage: errorMessage,
              ScreenshotBase64: screenshotBase64,
              ScreenshotMissing: screenshotMissing,
              TestRunId: testRunId




# Podman
cd hlx-testengine
podman build -t hlx-testengine -f Containerfile .
podman play kube kube.yaml
