from . import custom, generic, jobspy_board, simplify

FETCHERS = {
    "greenhouse": generic.greenhouse,
    "lever": generic.lever,
    "ashby": generic.ashby,
    "workday": generic.workday,
    "eightfold": generic.eightfold,
    "smartrecruiters": generic.smartrecruiters,
    "oraclecloud": generic.oraclecloud,
    "amazon": custom.amazon,
    "microsoft": custom.microsoft,
    "google": custom.google,
    "apple": custom.apple,
    "tesla": custom.tesla,
    "uber": custom.uber,
    "walmart": custom.walmart,
    "phenom": custom.phenom,
    "simplify": simplify.simplify,
    "jobspy": jobspy_board.jobspy,
}
