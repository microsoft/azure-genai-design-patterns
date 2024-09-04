FROM restreamio/gstreamer:x86_64-latest-prod-dbg
 
# Install Miniconda
RUN apt-get update && apt-get install -y wget bzip2
RUN wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O miniconda.sh
RUN bash miniconda.sh -b -p /opt/conda
ENV PATH=/opt/conda/bin:$PATH

# Copy your conda environment file
COPY conda.yml /tmp/conda.yml

# Create the conda environment
RUN conda env create -f /tmp/conda.yml

# Activate the conda environment
RUN echo "source activate $(head -1 /tmp/conda.yml | cut -d' ' -f2)" > ~/.bashrc