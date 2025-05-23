aws cloudformation deploy \
    --template-file ./template.yaml \
    --stack-name Agentic-Architecture-Stack \
    --capabilities CAPABILITY_NAMED_IAM \
    --region us-east-1 \
    --parameter-overrides "DBPassword=donationsmaster"

aws cloudformation delete-stack --stack-name Agentic-Architecture-Stack

wget https://d5wpal588audh.cloudfront.net/dataloader.sh
chmod +x dataloader.sh
./dataloader.sh

aws cloudformation deploy \
    --template-file ./template.yaml \
    --stack-name agentic-architecture-stack \
    --capabilities CAPABILITY_NAMED_IAM \
    --region us-east-1 \
    --parameter-overrides "DBPassword=donationsmaster"


sudo yum update -y
sudo yum install -y postgresql15
sudo yum install -y git

# Install dependencies
sudo yum groupinstall -y "Development Tools"
sudo yum install -y openssl-devel bzip2-devel libffi-devel xz-devel

# Download and compile Python 3.12
cd /opt
sudo wget https://www.python.org/ftp/python/3.12.0/Python-3.12.0.tgz
sudo tar xzf Python-3.12.0.tgz
cd Python-3.12.0
sudo ./configure --enable-optimizations
sudo make altinstall

# Get the latest datalaoder
wget https://d5wpal588audh.cloudfront.net/dataloader.sh
chmod +x dataloader.sh

# change this to our aws-samples/nonprofit-samples repo
#git clone https://github.com/aws-samples/agentic-architecture-using-bedrock.git
git clone git@ssh.gitlab.aws.dev:vtbloise/harnessing_agentic_ai_architecture_for_nonprofits.git

