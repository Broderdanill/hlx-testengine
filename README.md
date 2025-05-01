# Important!
This is under alot of work, do not consider this as a working application. There are alot to concider when testing BMC Helix Innovation Suite applications using this solution

# Description
This i a bundle of a kubernets deployment to automate testing from microsoft edge recordings (json) in a container with microsoft edge running tests in headless mode.
The tests will be triggered from a BMC Helix Innovation Studio application and the results after container has tested it will be returned to the Innovation Studio application aswell

# Build image with podman
podman build -t hlx-testengine -f Containerfile .

# Running with podman play kube
podman play kube kube.yaml

