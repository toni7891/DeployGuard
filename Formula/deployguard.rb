class Deployguard < Formula
  include Language::Python::Virtualenv

  desc "Scaffold, validate, cost, provision, and deploy services to Kubernetes safely"
  homepage "https://github.com/your-org/deployguard"
  url "https://github.com/your-org/deployguard/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "REPLACE_WITH_SHA256_OF_RELEASE_TARBALL"
  license "MIT"
  head "https://github.com/your-org/deployguard.git", branch: "main"

  # Runtime Python deps — generated via `brew extract` or `pip-audit`
  depends_on "python@3.12"

  # All external tool deps — brew handles version resolution
  depends_on "docker" => :recommended
  depends_on "helm"
  depends_on "infracost"
  depends_on "kubeconform"
  depends_on "kubernetes-cli"   # kubectl
  depends_on "minikube"
  depends_on "hashicorp/tap/terraform"
  depends_on "aquasecurity/trivy/trivy"

  # Python package resources — run `brew update-python-resources deployguard` to regenerate
  resource "typer" do
    url "https://files.pythonhosted.org/packages/typer/typer-0.12.3.tar.gz"
    sha256 "PLACEHOLDER"
  end

  resource "rich" do
    url "https://files.pythonhosted.org/packages/rich/rich-13.7.1.tar.gz"
    sha256 "PLACEHOLDER"
  end

  resource "pydantic" do
    url "https://files.pythonhosted.org/packages/pydantic/pydantic-2.7.1.tar.gz"
    sha256 "PLACEHOLDER"
  end

  resource "pydantic-settings" do
    url "https://files.pythonhosted.org/packages/pydantic-settings/pydantic_settings-2.2.1.tar.gz"
    sha256 "PLACEHOLDER"
  end

  resource "jinja2" do
    url "https://files.pythonhosted.org/packages/Jinja2/Jinja2-3.1.4.tar.gz"
    sha256 "PLACEHOLDER"
  end

  resource "sqlalchemy" do
    url "https://files.pythonhosted.org/packages/SQLAlchemy/SQLAlchemy-2.0.30.tar.gz"
    sha256 "PLACEHOLDER"
  end

  resource "kubernetes" do
    url "https://files.pythonhosted.org/packages/kubernetes/kubernetes-29.0.0.tar.gz"
    sha256 "PLACEHOLDER"
  end

  resource "boto3" do
    url "https://files.pythonhosted.org/packages/boto3/boto3-1.34.74.tar.gz"
    sha256 "PLACEHOLDER"
  end

  resource "requests" do
    url "https://files.pythonhosted.org/packages/requests/requests-2.31.0.tar.gz"
    sha256 "PLACEHOLDER"
  end

  resource "pyyaml" do
    url "https://files.pythonhosted.org/packages/PyYAML/PyYAML-6.0.1.tar.gz"
    sha256 "PLACEHOLDER"
  end

  resource "fastapi" do
    url "https://files.pythonhosted.org/packages/fastapi/fastapi-0.110.2.tar.gz"
    sha256 "PLACEHOLDER"
  end

  resource "uvicorn" do
    url "https://files.pythonhosted.org/packages/uvicorn/uvicorn-0.27.1.tar.gz"
    sha256 "PLACEHOLDER"
  end

  def install
    virtualenv_install_with_resources
  end

  def caveats
    <<~EOS
      DeployGuard is installed. Run the following to confirm your environment is ready:

        dg doctor

      Then scaffold your first service:

        dg init my-service
        dg cost
        dg provision
        dg deploy

      Docker Desktop must be running before provisioning or deploying.
      Install it from: https://docs.docker.com/desktop/install/mac-install/

      Full docs: https://github.com/your-org/deployguard
    EOS
  end

  test do
    assert_match "DeployGuard", shell_output("#{bin}/dg --help")
    system "#{bin}/dg", "--version"
  end
end
