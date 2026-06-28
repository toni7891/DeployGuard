class Deployguard < Formula
  include Language::Python::Virtualenv

  desc "Scaffold, validate, cost, provision, and deploy services to Kubernetes safely"
  homepage "https://github.com/toni7891/deployguard"
  url "https://github.com/toni7891/deployguard/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "3825499697df5c0b20f173f67bbb95df7a4ef6286379c0c5a9bb013f3d8a8767"
  license "MIT"
  head "https://github.com/toni7891/deployguard.git", branch: "main"

  depends_on "python@3.12"

  # Core toolchain — all from homebrew-core, no extra taps needed
  depends_on "docker" => :recommended
  depends_on "helm"
  depends_on "infracost"
  depends_on "kubeconform"
  depends_on "kubernetes-cli"
  depends_on "minikube"
  # terraform and trivy are third-party taps; install separately (dg doctor will guide you)

  # Python package resources — run `brew update-python-resources deployguard` to regenerate
  resource "aiohappyeyeballs" do
    url "https://files.pythonhosted.org/packages/33/c6/61a2d7b7572279226bb2e7f61d7a19ca7c90da0329c93fa0d560cbf288d8/aiohappyeyeballs-2.6.2.tar.gz"
    sha256 "e202810ee718bd01fc6ef49e8ea53d023d5cb6b581076d7925aa499fa55dbe64"
  end

  resource "aiohttp" do
    url "https://files.pythonhosted.org/packages/82/78/8ea7308cac6934de8c74a14f3d5f65d1c89287426688be79538d0e5c013d/aiohttp-3.14.1.tar.gz"
    sha256 "307f2cff90a764d329e77040603fa032db89c5c24fdad50c4c15334cba744035"
  end

  resource "aiosignal" do
    url "https://files.pythonhosted.org/packages/61/62/06741b579156360248d1ec624842ad0edf697050bbaf7c3e46394e106ad1/aiosignal-1.4.0.tar.gz"
    sha256 "f47eecd9468083c2029cc99945502cb7708b082c232f9aca65da147157b251c7"
  end

  resource "annotated-doc" do
    url "https://files.pythonhosted.org/packages/57/ba/046ceea27344560984e26a590f90bc7f4a75b06701f653222458922b558c/annotated_doc-0.0.4.tar.gz"
    sha256 "fbcda96e87e9c92ad167c2e53839e57503ecfda18804ea28102353485033faa4"
  end

  resource "annotated-types" do
    url "https://files.pythonhosted.org/packages/ee/67/531ea369ba64dcff5ec9c3402f9f51bf748cec26dde048a2f973a4eea7f5/annotated_types-0.7.0.tar.gz"
    sha256 "aff07c09a53a08bc8cfccb9c85b05f1aa9a2a6f23728d790723543408344ce89"
  end

  resource "anyio" do
    url "https://files.pythonhosted.org/packages/3b/72/5562aabb8dd7181e8e860622a38bea08d17842b99ecd4c91f84ac95251b0/anyio-4.14.1.tar.gz"
    sha256 "8d648a3544c1a700e3ff78615cd679e4c5c3f149904287e73687b2596963629e"
  end

  resource "attrs" do
    url "https://files.pythonhosted.org/packages/9a/8e/82a0fe20a541c03148528be8cac2408564a6c9a0cc7e9171802bc1d26985/attrs-26.1.0.tar.gz"
    sha256 "d03ceb89cb322a8fd706d4fb91940737b6642aa36998fe130a9bc96c985eff32"
  end

  resource "boto3" do
    url "https://files.pythonhosted.org/packages/ff/9f/897287e955db0f50b12fd69ef45956e4fd2c7ddb48c736872f7ea2314443/boto3-1.43.36.tar.gz"
    sha256 "587d7ee92a12e440ad12b0e7f11f3358f0c4d65b19f64726efc94aaf194aff28"
  end

  resource "botocore" do
    url "https://files.pythonhosted.org/packages/7c/37/da9e7f6ca73ac73afd7f0bb7f238aa5daba35c081e98d7f48a7c399599c0/botocore-1.43.36.tar.gz"
    sha256 "4cae47d1b2d426316b85a0087d9e69e048f13bc003b5177d74639fe9dfd28205"
  end

  resource "certifi" do
    url "https://files.pythonhosted.org/packages/c9/c7/424b75da314c1045981bd9777432fad05a9e0c69daa4ed7e308bbaffe405/certifi-2026.6.17.tar.gz"
    sha256 "024c88eeec92ca068db80f02b8b07c9cef7b9fe261d1d535abfd5abd6f6af432"
  end

  resource "charset-normalizer" do
    url "https://files.pythonhosted.org/packages/e7/a1/67fe25fac3c7642725500a3f6cfe5821ad557c3abb11c9d20d12c7008d3e/charset_normalizer-3.4.7.tar.gz"
    sha256 "ae89db9e5f98a11a4bf50407d4363e7b09b31e55bc117b4f7d80aab97ba009e5"
  end

  resource "click" do
    url "https://files.pythonhosted.org/packages/76/d4/81420972a676e8ffea40450d8c8c92943e7218a78fe9b64359836cc9876b/click-8.4.2.tar.gz"
    sha256 "9a6cea6e60b17ebe0a44c5cc636d94f09bd66142c1cd7d8b4cd731c4917a15f6"
  end

  resource "durationpy" do
    url "https://files.pythonhosted.org/packages/9d/a4/e44218c2b394e31a6dd0d6b095c4e1f32d0be54c2a4b250032d717647bab/durationpy-0.10.tar.gz"
    sha256 "1fa6893409a6e739c9c72334fc65cca1f355dbdd93405d30f726deb5bde42fba"
  end

  resource "fastapi" do
    url "https://files.pythonhosted.org/packages/8a/c9/5e8defe249899c0dc900643695fc07829a67fc88b4ff2cdb03fcbdbf5a4b/fastapi-0.138.1.tar.gz"
    sha256 "96e3702dce09ee0dce48856135620d3d865ca684a79fe7513fd7b13a12f82862"
  end

  resource "frozenlist" do
    url "https://files.pythonhosted.org/packages/2d/f5/c831fac6cc817d26fd54c7eaccd04ef7e0288806943f7cc5bbf69f3ac1f0/frozenlist-1.8.0.tar.gz"
    sha256 "3ede829ed8d842f6cd48fc7081d7a41001a56f1f38603f9d49bf3020d59a31ad"
  end

  resource "h11" do
    url "https://files.pythonhosted.org/packages/01/ee/02a2c011bdab74c6fb3c75474d40b3052059d95df7e73351460c8588d963/h11-0.16.0.tar.gz"
    sha256 "4e35b956cf45792e4caa5885e69fba00bdbc6ffafbfa020300e549b208ee5ff1"
  end

  resource "idna" do
    url "https://files.pythonhosted.org/packages/cd/63/9496c57188a2ee585e0f1db071d75089a11e98aa86eb99d9d7618fc1edce/idna-3.18.tar.gz"
    sha256 "ffb385a7e039654cef1ab9ef32c6fafe283c0c0467bba1d9029738ce4a14a848"
  end

  resource "jinja2" do
    url "https://files.pythonhosted.org/packages/df/bf/f7da0350254c0ed7c72f3e33cef02e048281fec7ecec5f032d4aac52226b/jinja2-3.1.6.tar.gz"
    sha256 "0137fb05990d35f1275a587e9aee6d56da821fc83491a0fb838183be43f66d6d"
  end

  resource "jmespath" do
    url "https://files.pythonhosted.org/packages/d3/59/322338183ecda247fb5d1763a6cbe46eff7222eaeebafd9fa65d4bf5cb11/jmespath-1.1.0.tar.gz"
    sha256 "472c87d80f36026ae83c6ddd0f1d05d4e510134ed462851fd5f754c8c3cbb88d"
  end

  resource "kubernetes" do
    url "https://files.pythonhosted.org/packages/2f/57/8b538af5076bc3372949d76f70ba3449bdfe52f9e6488170fa5d4f7cbe70/kubernetes-36.0.2.tar.gz"
    sha256 "03551fcb49cae1f708f63624041e37403545b7aaed10cbf54e2b01a37a5438e3"
  end

  resource "markdown-it-py" do
    url "https://files.pythonhosted.org/packages/06/ff/7841249c247aa650a76b9ee4bbaeae59370dc8bfd2f6c01f3630c35eb134/markdown_it_py-4.2.0.tar.gz"
    sha256 "04a21681d6fbb623de53f6f364d352309d4094dd4194040a10fd51833e418d49"
  end

  resource "markupsafe" do
    url "https://files.pythonhosted.org/packages/7e/99/7690b6d4034fffd95959cbe0c02de8deb3098cc577c67bb6a24fe5d7caa7/markupsafe-3.0.3.tar.gz"
    sha256 "722695808f4b6457b320fdc131280796bdceb04ab50fe1795cd540799ebe1698"
  end

  resource "mdurl" do
    url "https://files.pythonhosted.org/packages/d6/54/cfe61301667036ec958cb99bd3efefba235e65cdeb9c84d24a8293ba1d90/mdurl-0.1.2.tar.gz"
    sha256 "bb413d29f5eea38f31dd4754dd7377d4465116fb207585f97bf925588687c1ba"
  end

  resource "multidict" do
    url "https://files.pythonhosted.org/packages/1a/c2/c2d94cbe6ac1753f3fc980da97b3d930efe1da3af3c9f5125354436c073d/multidict-6.7.1.tar.gz"
    sha256 "ec6652a1bee61c53a3e5776b6049172c53b6aaba34f18c9ad04f82712bac623d"
  end

  resource "oauthlib" do
    url "https://files.pythonhosted.org/packages/0b/5f/19930f824ffeb0ad4372da4812c50edbd1434f678c90c2733e1188edfc63/oauthlib-3.3.1.tar.gz"
    sha256 "0f0f8aa759826a193cf66c12ea1af1637f87b9b4622d46e866952bb022e538c9"
  end

  resource "propcache" do
    url "https://files.pythonhosted.org/packages/ec/44/c87281c333769159c50594f22610f77398a47ccbfbbf23074e744e86f87c/propcache-0.5.2.tar.gz"
    sha256 "01c4fc7480cd0598bb4b57022df55b9ca296da7fc5a8760bd8451a7e63a7d427"
  end

  resource "pydantic" do
    url "https://files.pythonhosted.org/packages/18/a5/b60d21ac674192f8ab0ba4e9fd860690f9b4a6e51ca5df118733b487d8d6/pydantic-2.13.4.tar.gz"
    sha256 "c40756b57adaa8b1efeeced5c196f3f3b7c435f90e84ea7f443901bec8099ef6"
  end

  on_arm do
    resource "pydantic-core" do
      url "https://files.pythonhosted.org/packages/19/95/6195171e385007300f0f5574592e467c568becce2d937a0b6804f218bc49/pydantic_core-2.46.4-cp312-cp312-macosx_11_0_arm64.whl"
      sha256 "962ccbab7b642487b1d8b7df90ef677e03134cf1fd8880bf698649b22a69371f"
    end
  end

  on_intel do
    resource "pydantic-core" do
      url "https://files.pythonhosted.org/packages/ce/8c/af022f0af448d7747c5154288d46b5f2bc5f17366eaa0e23e9aa04d59f3b/pydantic_core-2.46.4-cp312-cp312-macosx_10_12_x86_64.whl"
      sha256 "3245406455a5d98187ec35530fd772b1d799b26667980872c8d4614991e2c4a2"
    end
  end

  resource "pydantic-settings" do
    url "https://files.pythonhosted.org/packages/5c/b5/8f48e906c3e0205276e8bd8cb7512217a87b2685304d64be27cad5b3019f/pydantic_settings-2.14.2.tar.gz"
    sha256 "c19dd64b19097f1de80184f0cc7b0272a13ae6e170cbf240a3e27e381ed14a5f"
  end

  resource "pygments" do
    url "https://files.pythonhosted.org/packages/c3/b2/bc9c9196916376152d655522fdcebac55e66de6603a76a02bca1b6414f6c/pygments-2.20.0.tar.gz"
    sha256 "6757cd03768053ff99f3039c1a36d6c0aa0b263438fcab17520b30a303a82b5f"
  end

  resource "python-dateutil" do
    url "https://files.pythonhosted.org/packages/66/c0/0c8b6ad9f17a802ee498c46e004a0eb49bc148f2fd230864601a86dcf6db/python-dateutil-2.9.0.post0.tar.gz"
    sha256 "37dd54208da7e1cd875388217d5e00ebd4179249f90fb72437e91a35459a0ad3"
  end

  resource "python-dotenv" do
    url "https://files.pythonhosted.org/packages/82/ed/0301aeeac3e5353ef3d94b6ec08bbcabd04a72018415dcb29e588514bba8/python_dotenv-1.2.2.tar.gz"
    sha256 "2c371a91fbd7ba082c2c1dc1f8bf89ca22564a087c2c287cd9b662adde799cf3"
  end

  resource "pyyaml" do
    url "https://files.pythonhosted.org/packages/05/8e/961c0007c59b8dd7729d542c61a4d537767a59645b82a0b521206e1e25c2/pyyaml-6.0.3.tar.gz"
    sha256 "d76623373421df22fb4cf8817020cbb7ef15c725b9d5e45f17e189bfc384190f"
  end

  resource "requests" do
    url "https://files.pythonhosted.org/packages/ac/c3/e2a2b89f2d3e2179abd6d00ebd70bff6273f37fb3e0cc209f48b39d00cbf/requests-2.34.2.tar.gz"
    sha256 "f288924cae4e29463698d6d60bc6a4da69c89185ad1e0bcc4104f584e960b9ed"
  end

  resource "requests-oauthlib" do
    url "https://files.pythonhosted.org/packages/42/f2/05f29bc3913aea15eb670be136045bf5c5bbf4b99ecb839da9b422bb2c85/requests-oauthlib-2.0.0.tar.gz"
    sha256 "b3dffaebd884d8cd778494369603a9e7b58d29111bf6b41bdc2dcd87203af4e9"
  end

  resource "rich" do
    url "https://files.pythonhosted.org/packages/c0/8f/0722ca900cc807c13a6a0c696dacf35430f72e0ec571c4275d2371fca3e9/rich-15.0.0.tar.gz"
    sha256 "edd07a4824c6b40189fb7ac9bc4c52536e9780fbbfbddf6f1e2502c31b068c36"
  end

  resource "s3transfer" do
    url "https://files.pythonhosted.org/packages/f6/94/dcdaeb1713cab9c84def276cfac7388b17c7d9855bbcfe88d77e4dbafd44/s3transfer-0.19.0.tar.gz"
    sha256 "ce436931687addc4c1712d52d40b32f53e88315723f107ffa20ba82b05a0f685"
  end

  resource "shellingham" do
    url "https://files.pythonhosted.org/packages/58/15/8b3609fd3830ef7b27b655beb4b4e9c62313a4e8da8c676e142cc210d58e/shellingham-1.5.4.tar.gz"
    sha256 "8dbca0739d487e5bd35ab3ca4b36e11c4078f3a234bfce294b0a0291363404de"
  end

  resource "six" do
    url "https://files.pythonhosted.org/packages/94/e7/b2c673351809dca68a0e064b6af791aa332cf192da575fd474ed7d6f16a2/six-1.17.0.tar.gz"
    sha256 "ff70335d468e7eb6ec65b95b99d3a2836546063f63acc5171de367e834932a81"
  end

  resource "sqlalchemy" do
    url "https://files.pythonhosted.org/packages/02/f1/a7a892f18d4d224e6b26f706531eafccc41e37594d37d304786969ee13cb/sqlalchemy-2.0.51.tar.gz"
    sha256 "804dccd8a4a6242c4e30ad961e540e18a588f6527202f2d6791b01845d59fdc9"
  end

  resource "starlette" do
    url "https://files.pythonhosted.org/packages/eb/e3/7c1dc7381d9f8ab7d854328ebfa884e62cb3f3d8549ddfd37c7814f42afa/starlette-1.3.1.tar.gz"
    sha256 "05d0213193f2fbaae60e2ecb593b4add4262ad4e46536b54abe36f11a71724e0"
  end

  resource "typer" do
    url "https://files.pythonhosted.org/packages/7c/f7/68adc395201b20b872d68e975386832e8005ffeacedd43a1d837a32815be/typer-0.26.8.tar.gz"
    sha256 "c244a6bd558886fe3f8780efb6bdd28bb9aff005a94eedebaa5cb32926fe2f7e"
  end

  resource "typing-extensions" do
    url "https://files.pythonhosted.org/packages/72/94/1a15dd82efb362ac84269196e94cf00f187f7ed21c242792a923cdb1c61f/typing_extensions-4.15.0.tar.gz"
    sha256 "0cea48d173cc12fa28ecabc3b837ea3cf6f38c6d1136f85cbaaf598984861466"
  end

  resource "typing-inspection" do
    url "https://files.pythonhosted.org/packages/55/e3/70399cb7dd41c10ac53367ae42139cf4b1ca5f36bb3dc6c9d33acdb43655/typing_inspection-0.4.2.tar.gz"
    sha256 "ba561c48a67c5958007083d386c3295464928b01faa735ab8547c5692e87f464"
  end

  resource "urllib3" do
    url "https://files.pythonhosted.org/packages/53/0c/06f8b233b8fd13b9e5ee11424ef85419ba0d8ba0b3138bf360be2ff56953/urllib3-2.7.0.tar.gz"
    sha256 "231e0ec3b63ceb14667c67be60f2f2c40a518cb38b03af60abc813da26505f4c"
  end

  resource "uvicorn" do
    url "https://files.pythonhosted.org/packages/c4/1f/fa18009dea8469069cca78a4e877a008ab78f08b064bfc9ab891579077ff/uvicorn-0.49.0.tar.gz"
    sha256 "ebf4271aa580d9de97f93192d4595176df6e91f9aae919ca73e4fc07df1e66a3"
  end

  resource "websocket-client" do
    url "https://files.pythonhosted.org/packages/2c/41/aa4bf9664e4cda14c3b39865b12251e8e7d239f4cd0e3cc1b6c2ccde25c1/websocket_client-1.9.0.tar.gz"
    sha256 "9e813624b6eb619999a97dc7958469217c3176312b3a16a4bd1bc7e08a46ec98"
  end

  resource "yarl" do
    url "https://files.pythonhosted.org/packages/79/12/1e8f37460ea0f7eb59c221fdaf0ed75e7ac43e97f8093b9c6f411df50a78/yarl-1.24.2.tar.gz"
    sha256 "9ac374123c6fd7abf64d1fec93962b0bd4ee2c19751755a762a72dd96c0378f8"
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

      Full docs: https://github.com/toni7891/deployguard
    EOS
  end

  test do
    assert_match "DeployGuard", shell_output("#{bin}/dg --help")
    system "#{bin}/dg", "--version"
  end
end
