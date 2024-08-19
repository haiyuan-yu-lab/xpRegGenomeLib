#!/usr/bin/env python

import os
import re
import sys
import pyBigWig
import argparse
import numpy as np
import pandas as pd

from RGTools.utils import str2bool
from RGTools.BedTable import BedTable3, BedTable6, BedTable6Plus

def BedTable6Gene():
    '''
    Helper function to return a BedTable6Plus object
    that can load bed6gene annotations.
    '''
    bt = BedTable6Plus(extra_column_names=["gene_name"], 
                       extra_column_dtype=[str], 
                       )

    return bt

#TODO: add BedTableTRE support
REGION_FILE_SUFFIX2CLASS_DICT = {
    "bed3": BedTable3,
    "bed6": BedTable6,
    "bed6gene": BedTable6Gene, 
}

def set_parser(parser):
    parser.add_argument("--job_name", 
                        help="Name of the job (also output file prefix).", 
                        required=True,
                        type=str, 
                        dest="job_name", 
                        )

    parser.add_argument("--sample", 
                        help="Sample name.", 
                        action="append", 
                        required=True, 
                        type=str, 
                        dest="sample_names", 
                        )

    parser.add_argument("--bw_pl", 
                        help="bigwig file for plus strand (must match sample order).", 
                        action="append", 
                        required=True, 
                        type=str, 
                        dest="bw_pls", 
                        )

    parser.add_argument("--bw_mn", 
                        help="bigwig file for minus strand (must match sample order).", 
                        action="append", 
                        type=str, 
                        dest="bw_mns", 
                        )
    
    parser.add_argument("--single_bw", 
                        help="If there is only plus strand bigwig file. If True, bw_mn will be ignored "
                             "and ignored_strandness will be set to True.", 
                        type=str2bool, 
                        dest="single_bw", 
                        default=False,
                        )

    parser.add_argument("--region_file_path", 
                        help="Path to the region file.", 
                        type=str, 
                        required=True, 
                        dest="region_file_path", 
                        )
    
    parser.add_argument("--region_file_type.", 
                        help="type of regional file. [bed3]"
                        "(options: {})".format(", ".join(REGION_FILE_SUFFIX2CLASS_DICT.keys())), 
                        default="bed3", 
                        type=str, 
                        dest="region_file_type", 
                        )

    parser.add_argument("--opath", 
                        help="Output path.", 
                        type=str, 
                        required=True, 
                        dest="opath", 
                        )
    
    parser.add_argument("--ignore_strandness", 
                        help="If to ignore strandness.", 
                        type=str2bool, 
                        default=False, 
                        dest="ignore_strandness", 
                        )
    
    parser.add_argument("--l_pad",
                        help="Padding for the left side of the region. [0]"
                             "Positive values will expand the region, "
                             "negative values will shrink the region.",
                        type=int,
                        default=0,
                        dest="l_pad",
                        )
    
    parser.add_argument("--r_pad",
                        help="Padding for the right side of the region. [0]"
                             "Positive values will expand the region, "
                             "negative values will shrink the region.",
                        type=int,
                        default=0,
                        dest="r_pad",
                        )
    
    parser.add_argument("--min_len_after_padding",
                        help="Minimum length of the region after padding. [0]", 
                        type=int, 
                        default=50, 
                        dest="min_len_after_padding", 
                        )
    
    parser.add_argument("--method_resolving_invalid_padding",
                        help="Method to resolve invalid padding. [raise]"
                             "Options: "
                             "- raise: raise an exception, "
                             "- fallback: give up on padding.", 
                        type=str, 
                        default="raise", 
                        dest="method_resolving_invalid_padding", 
                        )
    
    parser.add_argument("--output_type",
                        help="What information is outputted. "
                             "Options: [raw_count, RPK].",
                        type=str,
                        default="raw_count",
                        dest="output_type",
                        )

def args_check_and_preprocessing(args):
    '''
    Check the validity of the input arguments and preprocess them.
    
    Returns the preprocessed arguments.
    Argument list:
    - sample_names: list of sample names
    - bw_pls: list of paths to plus strand bigwig files
    - bw_mns: list of paths to minus strand bigwig files
    - region_file_path: path to the region file
    - region_file_type: type of the region file
    - opath: output path
    - ignore_strandness: if to ignore strandness
    - region_padding: padding for each region (string format).
    - output_type: what information is outputted
    - l_pad: left padding
    - r_pad: right padding
    '''
    if args.single_bw:
        # set bw_mns the same as bw_pls for input consistency
        args.bw_mns = args.bw_pls

        # ignore strandness
        if not args.ignore_strandness:
            raise Exception("Strandness is not ignored while single_bw is set to True.")

    if len(args.sample_names) != len(args.bw_pls) or len(args.sample_names) != len(args.bw_mns):
        raise Exception("Number of samples do not match the number of bigwig files.")
    
    if not os.path.exists(args.region_file_path):
        raise Exception("Region file does not exist.")
    
    if not os.path.exists(args.opath):
        os.makedirs(args.opath)

    if not args.region_file_type in REGION_FILE_SUFFIX2CLASS_DICT.keys():
        raise Exception("Unsupported region file type ({}).".format(args.region_file_type))

    if not args.output_type in ["raw_count", "RPK"]:
        raise Exception("Unsupported output type ({}).".format(args.output_type))

    if not args.min_len_after_padding >= 1:
        raise Exception("Minimum length after padding should be positive.")

    if not args.method_resolving_invalid_padding in ["raise", "fallback"]:
        raise Exception("Unsupported method to resolve invalid padding ({}).".format(args.method_resolving_invalid_padding))

    return args

def parse_region_input(region_file, file_type):
    '''
    Parse the input file and return a dataframe include region info.
    The returned df could have type-specific additional information that can be 
    used in further analysis, but the followings are always included:
    - "chrom"
    - "start"
    - "end"
    - "name"
    - "score"
    - "strand"
    
    The start and end loc will following the bed file convention (0-base, half-open).
    The index of the region df will be the unique identifier for the region 
    in the output matrix.
    '''
    if not file_type in REGION_FILE_SUFFIX2CLASS_DICT.keys():
        raise Exception("Unsupported region file type ({}).".format(file_type))
    
    region_bed_table = REGION_FILE_SUFFIX2CLASS_DICT[file_type]()
    region_bed_table.load_from_file(region_file)
    
    if file_type == "bed3":
        new_region_bed_table = BedTable6()
        new_region_bed_table.load_from_BedTable3(region_bed_table)
        region_bed_table = new_region_bed_table

    #TODO: Switch from df to BedTables
    region_df = region_bed_table.to_dataframe()
    region_df.fillna(".", inplace=True)
    region_df.index = region_df["chrom"] + "_" + region_df["start"].transform(str) + "_" + region_df["end"].transform(str)

    return region_df

def count_single_region(bw_pl, bw_mn, chrom, 
                        start, end, strand, 
                        single_bw=False,
                        output_type="raw_count", 
                        l_pad=0, r_pad=0, 
                        min_len_after_padding=50,
                        method_resolving_invalid_padding="raise",
                        ):
    '''
    Count the reads in a single region.
    Return the counts.

    Keyword arguments:
    - bw_pl: pyBigWig object for plus strand
    - bw_mn: pyBigWig object for minus strand
    - chrom: chromosome
    - start: start position
    - end: end position
    - strand: strandness, "+" or "-" or "."
    - single_bw: if there is only plus strand bigwig file
    - output_type: what information is outputted [raw_count, RPK]
    - l_pad: left padding
    - r_pad: right padding
    - min_len_after_padding: minimum length of the region after padding
    - method_resolving_invalid_padding: method to resolve invalid padding
    '''
    if - l_pad - r_pad + min_len_after_padding > end - start:
        if method_resolving_invalid_padding == "fallback":
            start = start
            end = end
        elif method_resolving_invalid_padding == "raise":
            raise Exception("Padding is larger than the region ({}:{:d}-{:d}).".format(chrom, start, end))
        else:
            raise Exception("Unsupported method to resolve invalid padding ({}).".format(method_resolving_invalid_padding))
    else: 
        start = start - l_pad
        end = end + r_pad

    pl_count = np.nan_to_num(bw_pl.values(chrom, 
                                          start, 
                                          end, 
                                          ), 
                             ).sum()
    if single_bw:
        mn_count = 0

        if not strand == ".":
            raise Exception("Strandness is not ignored while single_bw is set to True.")
    else:
        mn_count = -np.nan_to_num(bw_mn.values(chrom, 
                                            start, 
                                            end, 
                                            ), 
                                ).sum()

    if strand == "+":
        count = pl_count
    elif strand == "-":
        count = mn_count
    elif strand == ".":
        count = pl_count + mn_count

    if output_type == "raw_count":
        output = count
    elif output_type == "RPK":
        region_length = end - start
        output = count / region_length * 1000

    return output

def main(args):
    args = args_check_and_preprocessing(args)

    region_df = parse_region_input(args.region_file_path, 
                                   args.region_file_type, 
                                   )

    if args.ignore_strandness:
        region_df["strand"] = "."

    count_df = pd.DataFrame(index=region_df.index, 
                            columns=args.sample_names, 
                            dtype=int, 
                            )


    for sample ,bw_pl_path, bw_mn_path in zip(args.sample_names, args.bw_pls, args.bw_mns):
        bw_pl = pyBigWig.open(bw_pl_path)
        if args.single_bw:
            bw_mn = bw_pl # For compatibility, set bw_mn as the same as bw_pl
        else:
            bw_mn = pyBigWig.open(bw_mn_path)

        for region_id, region_info in region_df.iterrows():
            count_df.loc[region_id, sample] = count_single_region(bw_pl,
                                                                    bw_mn,
                                                                    region_info["chrom"],
                                                                    region_info["start"],
                                                                    region_info["end"],
                                                                    region_info["strand"],
                                                                    args.single_bw,
                                                                    args.output_type,
                                                                    args.l_pad,
                                                                    args.r_pad,
                                                                    args.min_len_after_padding,
                                                                    args.method_resolving_invalid_padding,
                                                                    )
        
        bw_pl.close()
        bw_mn.close()

    if args.output_type == "RPK":
        count_df.to_csv(os.path.join(args.opath, args.job_name + ".RPK.csv"))
    elif args.output_type == "raw_count":
        count_df.to_csv(os.path.join(args.opath, args.job_name + ".count.csv"))

    region_df.to_csv(os.path.join(args.opath, args.job_name + ".region_info.csv"))

if __name__ == "__main__":
    parser = argparse.ArgumentParser("Extracting Counts from BWs")
    set_parser(parser=parser)
    args = parser.parse_args()

    main(args)
