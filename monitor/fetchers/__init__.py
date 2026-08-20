from . import custom, generic, simplify

FETCHERS = {
    "greenhouse": generic.greenhouse,
    "lever": generic.lever,
    "ashby": generic.ashby,
    "workday": generic.workday,
    "eightfold": generic.eightfold,
    "smartrecruiters": generic.smartrecruiters,
    "amazon": custom.amazon,
    "microsoft": custom.microsoft,
    "google": custom.google,
    "apple": custom.apple,
    "tesla": custom.tesla,
    "uber": custom.uber,
    "simplify": simplify.simplify,
}
