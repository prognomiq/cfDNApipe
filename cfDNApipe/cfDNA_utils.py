# -*- coding: utf-8 -*-
"""
Created on Fri Aug  9 11:37:46 2019

@author: zhang
"""


from collections import Iterable
import pysam, pybedtools, os, subprocess, sys
from collections import defaultdict
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import gzip, shutil

__metaclass__ = type


class commonError(Exception):
    def __init__(self, message):
        self.message = message
        

# https://stackoverflow.com/questions/2158395/flatten-an-irregular-list-of-lists
def flatten(l):
    for el in l:
        if isinstance(el, Iterable) and not isinstance(el, (str, bytes)):
            yield from flatten(el)
        else:
            yield el
            
def isAlphaOrDigit(x):
    if x.isalpha() or x.isdigit():
        return True
    else:
        return False
    
def rmEndString(x, y):
    for item in y:
        if x.endswith(item):
            x = x.replace(item, '')
    
    return x


def isSoftClipped(cigar):
    """
    see here for more information about this function
    references:
        https://pysam.readthedocs.io/en/latest/api.html
        https://davetang.org/wiki/tiki-index.php?page=SAM
    """
    for (op,count) in cigar:
        if op in [4,5,6]: return True
    return False


def read_pair_generator(bam, region_string = None):
    """
    Generate read pairs in a BAM file or within a region string.
    Reads are added to read_dict until a pair is found.
    reference:
        https://www.biostars.org/p/306041/
    """
    read_dict = defaultdict(lambda: [None, None])
    for read in bam.fetch(region=region_string):
        # filter reads
        if read.is_unmapped or read.is_qcfail or read.is_duplicate: continue
        if not read.is_paired: continue
        if not read.is_proper_pair: continue
        if read.is_secondary or read.is_supplementary: continue
        if read.mate_is_unmapped: continue
        if read.rnext != read.tid: continue
        if read.template_length == 0: continue
        if isSoftClipped(read.cigar): continue
        
        qname = read.query_name
        if qname not in read_dict:
            if read.is_read1:
                read_dict[qname][0] = read
            else:
                read_dict[qname][1] = read
        else:
            if read.is_read1:
                yield read, read_dict[qname][1]
            else:
                yield read_dict[qname][0], read
            del read_dict[qname]


def bamTobed(bamInput = None, bedOutput = None, compress = True):
    # generate temp file for sorting and indexing
    bedOutput_path = os.path.realpath(bedOutput)
    this_pid = os.getpid()
    tmp_split = os.path.splitext(bedOutput_path)
    tmp_bedOutput = tmp_split[0] + "-temp-" + str(this_pid) + tmp_split[1]
    
    bai = bamInput + ".bai"
    if not os.path.exists(bai):
        message = "Index file " + bai + " do not exist!"
        raise commonError(message)
    
    bedWrite = open(tmp_bedOutput, "w")
    
    input_file = pysam.Samfile(bamInput, "rb")
    chr_reference = input_file.references
    for read1, read2 in read_pair_generator(input_file):
        read1Start = read1.reference_start
        read1End = read1.reference_end
        read2Start = read2.reference_start
        read2End = read2.reference_end
        
        if not read1.is_reverse:  # read1 is forward strand, read2 is reverse strand
            rstart = read1Start  # 0-based left-most site
            rend = read2End
        else:  # read1 is reverse strand, read2 is forward strand
            rstart = read2Start  # 0-based left-most site
            rend = read1End
            
        if (rstart < 0) or (rend < 0) or (rstart >= rend): continue
    
        tmp_str = chr_reference[read1.tid] + "\t" + str(rstart) + "\t" +str(rend) + "\n"
        bedWrite.write(tmp_str)
    
    bedWrite.close()
    print("Fragments generated, waitting for sorting......")
    
    
    bedData = pybedtools.BedTool(tmp_bedOutput)
    bedData.sort(output=bedOutput)
    
    os.remove(tmp_bedOutput)
    
    print("Fragments sorted.")
    
    if compress:
        print("Waitting for compressing and indexing......")
        bedgzfile = bedOutput + ".gz"
        pysam.tabix_compress(bedOutput, bedgzfile, force=False)
        pysam.tabix_index(bedgzfile, preset="bed", zerobased=True)
        print("Indexing bedgz file finished!")
    
    return True


# plot length distribution
def fraglendistribution(bedInput = None, plotOutput = None, binOutput = None, maxLimit = None):
    data = pd.read_table(bedInput, sep="\t", header = None,
                         names = ["chr", "start", "end"])
    len_info = np.asarray(data['end'] - data['start'])
    
    np.save(binOutput, len_info)
    
    len_info = len_info[np.where(len_info <= maxLimit)]
    
    fig = plt.figure(figsize = (10, 8))
    plot_limit = len_info.max() - len_info.min()
    plt.hist(len_info, bins = plot_limit)
    plt.savefig(plotOutput)
    plt.close(fig)
    
    return True
    
    
# calculate methylation level for regions
def calcMethyl(bamInput, bedInput, txtOutput):
    bai = bamInput + ".bai"
    if not os.path.exists(bai):
        message = "Index file " + bai + " do not exist!"
        raise commonError(message)
    
    bam_input = pysam.Samfile(bamInput, "rb")
    regions = pd.read_csv(bedInput, sep = "\t", header = None, names = ["chr", "start", "end"])
    
    
    CXXname = ["unmCpG", "mCpG",
               "unmCHG", "mCHG",
               "unmCHH", "mCHH",
               "unmUNC", "mUNC"]
    d = dict.fromkeys(CXXname, 0)
    regions = regions.assign(**d)
    
    for index, row in regions.iterrows():
        count_data = [0, 0, 0, 0, 0, 0, 0, 0]
        for read in bam_input.fetch(reference = row["chr"], start = row["start"], end = row["end"]):
            CXXinfo = read.get_tag('XM')
            count_data[0] += CXXinfo.count("z")
            count_data[1] += CXXinfo.count("Z")
            count_data[2] += CXXinfo.count("x")
            count_data[3] += CXXinfo.count("X")
            count_data[4] += CXXinfo.count("h")
            count_data[5] += CXXinfo.count("H")
            count_data[6] += CXXinfo.count("u")
            count_data[7] += CXXinfo.count("U")
        
        regions.loc[index, CXXname] = count_data
    
    regions["mlCpG"] = regions["mCpG"] / (regions["mCpG"] + regions["unmCpG"])
    regions["mlCHG"] = regions["mCHG"] / (regions["mCHG"] + regions["unmCHG"])
    regions["mlCHH"] = regions["mCHH"] / (regions["mCHH"] + regions["unmCHH"])
    regions["mlUNC"] = regions["mUNC"] / (regions["mUNC"] + regions["unmUNC"])
    
    regions.to_csv(txtOutput, sep = "\t", header = True, index = False)


# uncompress gz file
def un_gz(gzfile):
    file = gzfile.replace(".gz", "")
    with gzip.open(gzfile, 'r') as f_in, open(file, 'wb') as f_out:
        shutil.copyfileobj(f_in, f_out)


# run a single command line
def cmdCall(cmdLine):
    proc = subprocess.Popen(cmdLine, shell = True, stdout = subprocess.PIPE, stderr = subprocess.STDOUT, universal_newlines = True)
    while True:
        nextline = proc.stdout.readline()
        if (nextline == '') and (proc.poll() is not None):
            break
        sys.stdout.write(nextline)
        sys.stdout.flush()
        
    output, error = proc.communicate()
    exitCode = proc.returncode

    if exitCode != 0:
        raise commonError('**********CMD running error**********')






