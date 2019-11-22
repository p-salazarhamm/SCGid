import argparse
import sys
import re
from collections import namedtuple
from scripts.module import Module
from scripts.modcomm import LoggingEntity, Head
from scripts.reuse import ReusableOutput, ReusableOutputManager, augustus_predict, nucleotide_blast, protein_blast
from scripts.dependencies import CaseDependency
from scripts.parsers import PathAction
from scripts.sequence import DNASequenceCollection, DNASequence, CDSConcatenate, revcomp

class Codons(Module, LoggingEntity, Head):
    def __init__(self, argdict = None):
        super().__init__(self.__class__)
        if argdict is not None:
            self.config.load_argdict(argdict)
        else:
            self.argparser = self.generate_argparser()
            self.parsed_args = self.argparser.parse_args()
            self.config.load_cmdline( self.parsed_args ) # Copy command line args defined by self.argparser to self.config
        
        self.config.reusable.populate(
                ReusableOutput(
                    arg = "gff3",
                    pattern = ".*[.]aug[.]out[.]gff3$",
                    genfunc = augustus_predict,
                    genfunc_args = {
                        "prefix": self.config.get("prefix"),
                        "nucl": self.config.get("nucl"),
                        "augustus_sp": self.config.get("augustus_sp"),
                        "outpath": f"{self.config.get('prefix')}.aug.out.gff3"
                        }
                    )
                )
        self.config.dependencies.populate(
                CaseDependency("augustus", "gff3", None),
            )
        
        if self.config.get("mode") == "blastp":
            self.config.reusable.add(
                ReusableOutput(
                    arg = "blastout",
                    pattern = ".*[.]spdb[.]blast[.]out$",
                    genfunc = protein_blast,
                    genfunc_args = {
                        "prot_path": self.config.get("prot"),
                        "db": self.config.get("spdb"),
                        "evalue": self.config.get("evalue"),
                        "cpus": self.config.get("cpus"),
                        "outpath": f"{self.config.get('prefix')}.spdb.blast.out"
                        }
                    )
            )
            self.config.dependencies.add(
                CaseDependency("blastp", "blastout", None)
            )

        elif self.config.get("mode") == "blastn":
            self.config.reusable.add(
                ReusableOutput(
                    arg = "blastout",
                    pattern = ".*[.]nt[.]blast[.]out$",
                    genfunc = nucleotide_blast,
                    genfunc_args = {
                        "nucl_path": self.config.get("nucl"),
                        "db": "nt",
                        "evalue": self.config.get("evalue"),
                        "cpus": self.config.get("cpus"),
                        "outpath": f"{self.config.get('prefix')}.nt.blast.out"
                        }
                    )
            )
            self.config.dependencies.add(
                CaseDependency("blastn", "blastout", None)
            )
        
        else:
            print("Bad mode selection.")
            sys.exit(1)
            
    
    def generate_argparser(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("mod", nargs="*")
        parser.add_argument('-m','--gff3', metavar='gene_models', action = PathAction, required = False, default = None, help ="A gff3 file from Augustus (one is generated by scgid blob) that contains the gene models for your metagenome.")
        parser.add_argument('-n','--nucl', metavar='contig_fasta', action = PathAction, required = True, help ="The contig fasta associated with your metagenome.")
        parser.add_argument('-p','--prot', metavar = "protein_fasta", action=PathAction, required=False, help = "A FASTA file containing the proteins called from the genome.")

        parser.add_argument('-g', '--targets', metavar = 'target_taxa', action='store', required=True, help="A comma-separated list with NO spaces of the taxonomic levels that the gc-coverage window should be chosen with respect to including. EXAMPLE: '-g Fungi,Eukaryota,Homo'")
        parser.add_argument('-x', '--exceptions', metavar = 'exceptions_to_target_taxa', action='store', required=False, default=None, help="A comma-separated list with NO spaces of any exlusions to the taxonomic levels specified in -g|--targets. For instance if you included Fungi in targets but want to exclude ascomycetes use: '-x Ascomycota'")
        parser.add_argument('-f','--prefix', metavar = 'prefix_for_output', required=False, default='scgid', help="The prefix that you would like to be used for all output files. DEFAULT = scgid")
        parser.add_argument('--cpus', metavar = 'cores', action = 'store', required = False, default = "1", help = "The number of cores available for BLAST to use.")
        parser.add_argument('--mode', metavar = "mode", action="store",required=False, default ='blastp', help = "The type of blast results that you would like to use to annotate the tips of the RSCU tree ('blastp' or 'blastn'). This module will automatically do a blastn search of the NCBI nt database for you. At this time, a blastp search can not be run directly from this script. INSTEAD, if using mode 'blastp' (DEFAULT, recommended) you must specify a scgid blob-derived _info_table.tsv file with -i|--infotable")
        parser.add_argument('--minlen', metavar = 'minlen', action = 'store', required = False, default = '3000', help = 'Minimum length of CDS concatenate to be kept and used to build RSCU tree. Highly fragmented assemblies will need this to be reduced. Reduce in response to `Tree too small.` error.')
        parser.add_argument('-sp','--augustus_sp', metavar = "augustus_species", action="store",required=False, default=None, help = "Augustus species for gene predicition. Type `augustus --species=help` for list of available species designations.")
        parser.add_argument('-e', '--evalue', metavar = 'e-value_cutoff', action = 'store', required = False, default = '1e-5', help = "The evalue cutoff for blast. Default: 1xe-5)")
        parser.add_argument('-b','--blastout', metavar = "blastout", action=PathAction, required=False, help = "The blast output file from a blastn search of the NCBI nt database with your contigs as query. If you have not done this yet, this script will do it for you.")
        parser.add_argument('-i','--infotable', metavar = "infotable", action=PathAction, required=False, help = "The scgid gc-cov-derived infotable generated by a blastp search of a swissprot-style protein database.")
        parser.add_argument("--noplot", action="store_true", default=False, required=False, help="Turns of plotting of annotated trees to PDF.")
        parser.add_argument('--Xmx', metavar = "available_memory", action="store",required=False, default = "2g", help = "Set memoray available to run ClaMs. Specicy as such: X megabytes = Xm, X gigabytes = Xg")

        return parser

    def extract_cds_gff3 (self, gff3_path, nucl):

        contig_chunks = {}

        with open (gff3_path, 'r') as gff3:

            CDSChunk = namedtuple("CDSChunk", ["start", "end", "strand"])
            for line in gff3:

                # Skip comment lines
                if line[0] == "#":
                    continue
                
                spl = line.split('\t')

                # Ignore all but CDS lines in gff3
                if spl[2] == "CDS":

                    shortname = '_'.join( spl[0].split('_')[0:2] )

                    # Capture pid     
                    s = re.search("[.](g[0-9]+)[.]",spl[8])
                    pid = s.group(1)

                    # Group CDS lines in gff3 by parent contig (by shortname) and protein (by pid)
                    if shortname in contig_chunks:
                        if pid in contig_chunks[shortname]:
                            contig_chunks[shortname][pid].append( CDSChunk(spl[3], spl[4], spl[6]) )
                        else:
                            contig_chunks[shortname][pid] = [ CDSChunk(spl[3], spl[4], spl[6]) ]
                    else:
                        contig_chunks[shortname] = {
                                pid: [ CDSChunk(spl[3], spl[4], spl[6]) ]
                                }

        cds_concatenates = {}

        # Iterate through CDS chunks of predicted proteins on each contig and pull CDS sequences from nucleotide fasta
        for shortname, pids in contig_chunks.items():

            contig_cds_cat = str()
            
            for pid, chunks in pids.items():

                gene_cds = str()

                for chunk in chunks:
                    
                    # Ignore zero-length CDS chunks
                    if chunk.start == chunk.end:
                        continue
                    
                    # Fetch CDS sequence from contig by start/stop indices listed in gff3
                    chunk_string = nucl.index[shortname].string[
                        int(chunk.start)-1: int(chunk.end)
                        ]

                    # Reverse complement chunk strings if they occur on reverse strand
                    if chunk.strand == '-':
                        chunk_string = revcomp(chunk_string)
                    
                    # Combine chunk sequences if they occur on the same gene
                    gene_cds += chunk_string

                # Toss out predicted CDS if they aren't divisible by 3 to avoid introducing frameshifts into CDS concatenate
                # Combine gene_cds into contig_cds_cat beacuse they occur on the same contig
                if len(gene_cds) % 3 == 0:
                    contig_cds_cat += gene_cds
            
            # Store contig_cds_cat in DNASequence object and add to dict
            if len(contig_cds_cat) != 0:
                cds_concatenates[shortname] = CDSConcatenate(shortname, contig_cds_cat)
        
        # Return all contig-level CDS concatenates as a DNASequenceCollection object
        return DNASequenceCollection().from_dict(cds_concatenates)

    def run(self):
        self.start_logging()
        self.setwd( __name__, self.config.get("prefix") )
        self.config.reusable.check()
        self.config.dependencies.check(self.config)

        self.logger.info(f"Running in {self.config.get('mode')} mode.")

        # Read in nucleotide FASTA
        nucl = DNASequenceCollection().from_fasta(self.config.get("nucl"))

        # Rekey nucl by shortname
        nucl.rekey_by_shortname()

        # Concatenate all CDS sequences on each contig
        cds_concatenates = self.extract_cds_gff3(
            self.config.get("gff3"),
            nucl
        )
        
        # Remove CDS concatenates shorter than supplied minlin
        cds_concatenates.remove_small_sequences( int(self.config.get("minlen")) )

        for c in cds_concatenates.seqs():
            c.count_codons()
        
        for c in cds_concatenates.seqs():
            print (c.codon_counts)